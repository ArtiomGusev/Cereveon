import json
import logging
import os
import time
from collections.abc import Iterator

import httpx

from llm import observability
from llm.log_config import game_id_var
from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS
from llm.rag.prompts.mode_2.render import render_mode_2_prompt
from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT
from llm.rag.validators.mode_2_negative import validate_mode_2_negative
from llm.rag.validators.explain_response_schema import EngineSignalSchema, ExplainSchemaError
from llm.seca.coach.confidence_language_controller import build_language_controller_block
from llm.rag.prompts.input_sanitizer import sanitize_user_query
from llm.rag.safety.output_firewall import check_output, OutputFirewallError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# DeepSeek configuration
#
# We talk to DeepSeek via its OpenAI-compatible chat-completions
# endpoint at {COACH_DEEPSEEK_API_BASE}/chat/completions.  All three
# values are env-overridable so a future model bump or alternate
# provider with the same wire format (Together, Groq, OpenAI proper)
# can be reached by changing env without editing code.
#
#   COACH_DEEPSEEK_API_BASE   default ``https://api.deepseek.com``
#   COACH_DEEPSEEK_MODEL      default ``deepseek-v4-flash`` (V4, non-thinking)
#   COACH_DEEPSEEK_API_KEY    required at runtime (no default)
#
# The Ollama variant of this module previously read
# ``COACH_OLLAMA_URL`` / ``COACH_OLLAMA_MODEL`` / ``COACH_OLLAMA_NUM_CTX``;
# those have been removed.  The deterministic fallback in
# chat_pipeline.py / live_move_pipeline.py keeps coaching available
# during DeepSeek outages, exactly as it did during Ollama outages.
# ---------------------------------------------------------

DEEPSEEK_API_BASE = os.getenv("COACH_DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_URL = f"{DEEPSEEK_API_BASE}/chat/completions"
MODEL_NAME = os.getenv("COACH_DEEPSEEK_MODEL", "deepseek-v4-flash")

#: Retry budget — sourced from ``llm.rag.llm.config.MAX_MODE_2_RETRIES``
#: so the four LLM-bearing pipelines stay in lock-step.  PR 11
#: (2026-05-15) consolidated four previously-independent literal
#: constants into this shared source.  Local name ``MAX_RETRIES``
#: preserved for backward-compat with ``test_explain_pipeline_retry.py``
#: which imports it directly.
from llm.rag.llm.config import (
    CHAT_MAX_COMPLETION_TOKENS as _CHAT_MAX_COMPLETION_TOKENS,
    MAX_MODE_2_RETRIES as _CONFIG_MAX_RETRIES,
)

MAX_RETRIES = _CONFIG_MAX_RETRIES
_RETRY_DELAY_SECONDS = 0.5

# Defense-in-depth cap on assembled streaming-response bytes.  The
# 120s timeout is the only other bound on a compromised provider's
# response size; a hostile DeepSeek-equivalent streaming tokens at
# near-line-rate could OOM the api container.  100 kB is ~25 k tokens
# — two orders of magnitude above the realistic Mode-2 response
# (sub-1 kB) but well below the api container's memory budget.
# Truncation at the cap is safe by construction: validators downstream
# (validate_mode_2_negative / structure / semantic / output_firewall)
# will either accept the truncated text on contract or reject it and
# fall through to the deterministic builder.  See PR 8 reviewer note
# for the threat-model rationale.
_MAX_STREAM_RESPONSE_BYTES = 100_000


# ---------------------------------------------------------
# LLM CALL
# ---------------------------------------------------------


class LLMConfigError(RuntimeError):
    """Raised when the LLM provider is misconfigured (e.g. missing API key)."""


def _estimate_tokens(text_len: int) -> int:
    """Crude chars→tokens estimate (~4 chars/token for English prose).

    Used ONLY when the provider's usage frame never arrived — an aborted
    stream (consumer rejected mid-generation) or a byte-cap truncation —
    so the attempt's real, billed spend still lands on the cost metric
    instead of silently reading zero.  Estimates are flagged
    ``usage_estimated`` in the ``llm_call`` log line.
    """
    return max(1, text_len // 4)


def _cache_split(usage: dict) -> tuple[int, int]:
    """Extract the prompt-cache (hit, miss) token split from a usage block.

    DeepSeek reports ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``
    at the top level; OpenAI-compatible gateways report
    ``prompt_tokens_details.cached_tokens`` (hit only — miss is derived).
    Returns ``(0, 0)`` when no split is reported or it doesn't add up to
    ``prompt_tokens``; callers then fall back to flat cache-miss pricing
    (the conservative pre-split behaviour).
    """
    try:
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        if hit is None:
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                hit = details.get("cached_tokens")
        if hit is None:
            return (0, 0)
        hit = int(hit)
        miss = int(miss) if miss is not None else prompt_tokens - hit
        if hit < 0 or miss < 0 or hit + miss != prompt_tokens:
            return (0, 0)
        return (hit, miss)
    except (TypeError, ValueError):
        return (0, 0)


def call_llm(prompt: str, *, max_completion_tokens: int | None = None) -> str:
    """Single-shot LLM completion against DeepSeek's chat-completions API.

    The Mode-2 prompt that arrives here is already a fully-rendered
    string (system + style + RAG + history + user query, assembled by
    ``render_mode_2_prompt``).  Rather than re-architect the prompt
    layer to use OpenAI-style multi-turn ``messages``, we wrap the
    rendered prompt as a single user message — the model receives the
    same content it would have received via Ollama's ``/api/generate``,
    just over a different wire format.

    Returns the assistant's reply text, stripped.  Raises:
      - ``LLMConfigError`` when ``COACH_DEEPSEEK_API_KEY`` is unset.
      - ``httpx.HTTPStatusError`` on non-2xx (caught upstream by the
        retry loop in ``generate_validated_explanation`` and the
        deterministic-fallback path in chat_pipeline / live_move).
      - ``httpx.HTTPError`` on empty / malformed streaming responses.

    Architecture: this function is the LLM Layer's only outbound
    point per ``docs/ARCHITECTURE.md`` ("LLM Generation
    (untrusted)").  Output validators downstream (Mode-2 negative,
    output firewall, ESV grounding) are unaffected by the provider
    swap — they validate the returned text regardless of where it
    came from.

    Wire format: as of 2026-05-14 (PR 8) the request uses
    ``stream: True`` and consumes DeepSeek's SSE response server-side
    rather than waiting for the full response in one shot.  Two
    benefits:

    - The HTTP connection to DeepSeek receives bytes continuously
      throughout generation — middleboxes / reverse proxies are less
      likely to close it as idle.
    - Server-side time-to-finish improves slightly on long responses
      because we process tokens as they arrive instead of waiting for
      the model to finalise the full body.

    The return type and downstream contract are unchanged: callers
    receive the full assembled string only after the stream ends.  The
    architecture's "validators run on full output before bytes reach
    the client" invariant is preserved end-to-end — see
    ``docs/ARCHITECTURE.md`` "Output Validation".  True token-level
    streaming to the client is intentionally not done here; see the
    PR 8 description for the architecture-trade-off discussion.
    """
    api_key = os.getenv("COACH_DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise LLMConfigError(
            "COACH_DEEPSEEK_API_KEY is unset; cannot call DeepSeek. "
            "Set it in .env.prod (or shell env in dev) and restart the api container."
        )

    request_body = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        # OpenAI-compatible SSE contract: when ``include_usage`` is true,
        # the final non-``[DONE]`` chunk carries ``{"usage": {...}}``
        # alongside an empty ``choices`` array.  Extracted below to feed
        # the LLM token / cost counters.
        "stream_options": {"include_usage": True},
    }
    if max_completion_tokens is not None:
        # Worst-case output-spend bound (see llm.rag.llm.config for the
        # per-pipeline caps and their headroom rationale).  Only sent when
        # the caller opts in, so the BaseLLM adapter / smoke-test wire
        # stays byte-identical to the historical request shape.
        request_body["max_tokens"] = max_completion_tokens
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    chunks: list[str] = []
    total_bytes = 0
    usage: dict[str, int] = {}
    start = time.perf_counter()
    outcome = "ok"
    error_category: str | None = None
    try:
        with httpx.stream(
            "POST",
            DEEPSEEK_URL,
            json=request_body,
            headers=headers,
            timeout=120,
        ) as response:
            response.raise_for_status()
            for raw_line in response.iter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    # Skip individual malformed chunks rather than
                    # crashing the whole call — the OpenAI-compatible
                    # SSE format occasionally interleaves keep-alive
                    # comments and zero-content delta frames that should
                    # not abort the stream.  A truly malformed response
                    # surfaces below via the empty-chunks check.
                    continue
                # ``usage`` is emitted on the final chunk when
                # ``stream_options.include_usage=true``; the same chunk
                # carries ``choices: []`` so the content extraction
                # below is a no-op for it.
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                try:
                    delta = event["choices"][0].get("delta", {})
                except (KeyError, IndexError, TypeError):
                    continue
                content = delta.get("content")
                if content:
                    chunks.append(content)
                    total_bytes += len(content)
                    if total_bytes > _MAX_STREAM_RESPONSE_BYTES:
                        # Defense-in-depth: stop consuming before the
                        # response can OOM the container.  Downstream
                        # validators will see truncated text and either
                        # accept on contract or fall through to the
                        # deterministic builder.
                        break
    except httpx.TimeoutException:
        outcome = "timeout"
        error_category = "timeout"
        raise
    except httpx.HTTPStatusError as exc:
        outcome = "http_error"
        status_code = exc.response.status_code if exc.response is not None else 0
        error_category = "http_5xx" if status_code >= 500 else "http_4xx"
        raise
    except httpx.HTTPError:
        outcome = "http_error"
        error_category = "transport"
        raise
    finally:
        duration = time.perf_counter() - start
        assembled_so_far = "".join(chunks).strip()
        if outcome == "ok" and not assembled_so_far:
            outcome = "empty"
            error_category = "empty"
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        cache_hit_tokens, cache_miss_tokens = _cache_split(usage)
        usage_estimated = False
        if outcome == "ok" and not usage and assembled_so_far:
            # The stream ended without a usage frame (the byte-cap break
            # fires before it arrives; some gateways omit it).  Estimate
            # so the billed spend doesn't read zero; no cache split is
            # attributable, so the estimate bills flat at the miss rate.
            prompt_tokens = _estimate_tokens(len(prompt))
            completion_tokens = _estimate_tokens(total_bytes)
            usage_estimated = True
        observability.observe_llm_call(
            model=MODEL_NAME,
            outcome=outcome,
            duration_seconds=duration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            error_category=error_category,
        )
        # Structured log line — Loki picks up the JSON-formatted record
        # via Alloy (monitoring/alloy.alloy).  The dashboard's cost-per-
        # match panel filters on ``event="llm_call"`` and aggregates
        # ``cost_usd`` by ``game_id``.  ``game_id`` is sourced from the
        # contextvar so handlers that don't carry one simply omit it.
        cost_usd = observability.cost_for_call(
            MODEL_NAME,
            prompt_tokens,
            completion_tokens,
            cache_hit_tokens,
            cache_miss_tokens,
        )
        logger.info(
            "llm_call",
            extra={
                "event": "llm_call",
                "game_id": game_id_var.get(),
                "model": MODEL_NAME,
                "outcome": outcome,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cache_hit_tokens": cache_hit_tokens,
                "cache_miss_tokens": cache_miss_tokens,
                "usage_estimated": usage_estimated,
                "cost_usd": round(cost_usd, 6),
                "latency_ms": round(duration * 1000, 3),
            },
        )

    assembled = "".join(chunks).strip()
    if not assembled:
        raise httpx.HTTPError(
            "DeepSeek streaming response yielded no content; "
            "expected at least one non-empty delta.content frame."
        )
    return assembled


def call_llm_stream(
    prompt: str, *, max_completion_tokens: int | None = None
) -> Iterator[str]:
    """Streaming variant of :func:`call_llm` — yields DeepSeek ``delta.content``
    chunks as they arrive instead of assembling them into one string.

    Identical wire contract, config, byte cap, and observability as
    ``call_llm``; the only difference is the return shape (generator of
    partial text vs. the full string).  Consumed by the validate-before-emit
    streaming pipeline (``chat_stream_pipeline.stream_chat_reply``), which is
    responsible for running the FORBID gates on the growing buffer before any
    chunk is forwarded to the client.

    Failure modes:
      - ``LLMConfigError`` raised eagerly (before any yield) when the key is
        unset.
      - ``httpx`` transport / status / timeout errors may surface *mid-
        iteration*; the caller must catch them and fall back deterministically
        (a partial stream has already validated-and-emitted only clean text,
        so aborting to the fallback is safe).
      - When the CONSUMER abandons the generator mid-stream (the streaming
        pipeline rejecting a FORBID violation and moving to a retry), the
        interpreter delivers ``GeneratorExit`` at the suspended ``yield``:
        the ``finally`` below records the attempt as outcome ``aborted``
        with ESTIMATED token usage (the provider's usage frame never
        arrived, but the prompt and the generated-so-far tokens were
        billed), and closing the ``httpx.stream`` context tears down the
        connection so DeepSeek stops generating (and billing) server-side.
    """
    api_key = os.getenv("COACH_DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise LLMConfigError(
            "COACH_DEEPSEEK_API_KEY is unset; cannot call DeepSeek. "
            "Set it in .env.prod (or shell env in dev) and restart the api container."
        )

    request_body = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_completion_tokens is not None:
        # Same opt-in output cap as ``call_llm`` (see llm.rag.llm.config).
        request_body["max_tokens"] = max_completion_tokens
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    total_bytes = 0
    completion_chars = 0
    usage: dict[str, int] = {}
    start = time.perf_counter()
    outcome = "ok"
    error_category: str | None = None
    try:
        with httpx.stream(
            "POST", DEEPSEEK_URL, json=request_body, headers=headers, timeout=120
        ) as response:
            response.raise_for_status()
            for raw_line in response.iter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                try:
                    delta = event["choices"][0].get("delta", {})
                except (KeyError, IndexError, TypeError):
                    continue
                content = delta.get("content")
                if content:
                    total_bytes += len(content)
                    if total_bytes > _MAX_STREAM_RESPONSE_BYTES:
                        # Defense-in-depth OOM cap — stop consuming.  The
                        # caller's end-of-stream REQUIRE check still runs on
                        # whatever was validated-and-emitted so far.
                        break
                    completion_chars += len(content)
                    yield content
    except GeneratorExit:
        # Consumer closed the generator mid-stream (validator rejection /
        # retry in the streaming pipeline).  The attempt still billed its
        # prompt plus everything generated so far — account for it below.
        outcome = "aborted"
        error_category = "aborted"
        raise
    except httpx.TimeoutException:
        outcome = "timeout"
        error_category = "timeout"
        raise
    except httpx.HTTPStatusError as exc:
        outcome = "http_error"
        status_code = exc.response.status_code if exc.response is not None else 0
        error_category = "http_5xx" if status_code >= 500 else "http_4xx"
        raise
    except httpx.HTTPError:
        outcome = "http_error"
        error_category = "transport"
        raise
    finally:
        duration = time.perf_counter() - start
        if outcome == "ok" and completion_chars == 0:
            outcome = "empty"
            error_category = "empty"
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        cache_hit_tokens, cache_miss_tokens = _cache_split(usage)
        usage_estimated = False
        if outcome in ("ok", "aborted") and not usage and completion_chars > 0:
            # No usage frame (aborted before it arrived / byte-cap break /
            # gateway omission) — estimate so the billed spend is visible.
            # No cache split is attributable; bills flat at the miss rate.
            prompt_tokens = _estimate_tokens(len(prompt))
            completion_tokens = _estimate_tokens(completion_chars)
            usage_estimated = True
        observability.observe_llm_call(
            model=MODEL_NAME,
            outcome=outcome,
            duration_seconds=duration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            error_category=error_category,
        )
        cost_usd = observability.cost_for_call(
            MODEL_NAME,
            prompt_tokens,
            completion_tokens,
            cache_hit_tokens,
            cache_miss_tokens,
        )
        logger.info(
            "llm_call",
            extra={
                "event": "llm_call",
                "game_id": game_id_var.get(),
                "model": MODEL_NAME,
                "outcome": outcome,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cache_hit_tokens": cache_hit_tokens,
                "cache_miss_tokens": cache_miss_tokens,
                "usage_estimated": usage_estimated,
                "cost_usd": round(cost_usd, 6),
                "latency_ms": round(duration * 1000, 3),
                "streamed": True,
            },
        )


# ---------------------------------------------------------
# SINGLE EXPLANATION ATTEMPT
# ---------------------------------------------------------


def generate_once(fen: str, stockfish_json: dict, user_query: str) -> tuple[str, dict]:
    """Generate one explanation attempt. *user_query* must already be sanitized
    by the caller (generate_validated_explanation is the authoritative point)."""
    esv = extract_engine_signal(stockfish_json, fen=fen)

    rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

    style_block = build_language_controller_block(esv)
    prompt = render_mode_2_prompt(
        system_prompt=SYSTEM_PROMPT + "\n\n" + style_block,
        engine_signal=esv,
        rag_docs=rag_docs,
        fen=fen,
        user_query=user_query,
    )

    # Same Mode-2 output-spend cap as the chat pipelines (cost insurance;
    # see llm.rag.llm.config).
    explanation = call_llm(prompt, max_completion_tokens=_CHAT_MAX_COMPLETION_TOKENS)

    # Post-LLM safety check — block before returning to caller
    check_output(explanation)

    return explanation, esv


# ---------------------------------------------------------
# VALIDATED EXPLANATION WITH RETRY
# ---------------------------------------------------------


def generate_validated_explanation(
    fen: str,
    stockfish_json: dict,
    user_query: str | None = "",
):
    # Authoritative sanitization point for the LLM pipeline.
    #
    # Not wired to any HTTP endpoint — ``/explain`` and ``/seca/explain``
    # are intentionally deterministic SAFE_V1 routes (free, fast,
    # CI-friendly); the Mode-2 LLM path is reached via ``/chat`` and
    # ``/chat/stream``, which call ``generate_chat_reply`` directly.
    # This function is alive as the test surface for the retry /
    # validate / fallback assembly (``test_firewall_integration.py``,
    # ``test_explain_pipeline_retry.py``) but has no live HTTP caller.
    #
    # The PR 10 doc-honesty pass aligned README + SECA.md with this
    # reality; the previous "forward-looking, not yet wired" comment
    # implied wiring was on the roadmap when it was actually a
    # deliberate non-choice.
    #
    # server.py schema validation is an independent early-rejection layer
    # (returns HTTP 422 before reaching here).  Both call sites are
    # intentional defence-in-depth; sanitize_user_query is idempotent for
    # clean inputs so the double-call is harmless.
    try:
        clean_query = sanitize_user_query(user_query or "")
    except ValueError:
        # Injection detected — return safe fallback without calling the LLM.
        esv = extract_engine_signal(stockfish_json, fen=fen)
        return (
            "I cannot process this request.",
            esv,
        )

    last_error = None
    # Mypy flags ``esv: dict = {}`` here as a redefinition of the
    # ``esv = extract_engine_signal(...)`` assignment in the injection-
    # detected branch above (lines 245-253), even though that branch
    # always returns before this line is reached.  Drop the redundant
    # type annotation — the assignment from the early-return branch
    # already establishes ``esv`` as a ``dict`` for mypy's narrowing,
    # and the loop below reassigns it on every attempt anyway.  The
    # bug only surfaced when study-plan's ``verdict.py`` (phase 2)
    # started importing ``validate_output``, which pulled this file
    # into mypy's reachable check set on incremental runs.
    esv = {}

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            time.sleep(_RETRY_DELAY_SECONDS)
        try:
            explanation, esv = generate_once(fen, stockfish_json, clean_query)
        except OutputFirewallError:
            # Output firewall blocked the response — treat as a hard failure,
            # no retry (the model produced unsafe content; another attempt is
            # unlikely to be safe).
            esv = extract_engine_signal(stockfish_json, fen=fen)
            return (
                "I cannot process this request.",
                esv,
            )

        try:
            validate_mode_2_negative(explanation)
            # Validate engine signal structural integrity before returning.
            # The ESV is produced by extract_engine_signal(); if the schema
            # check fails here it is a programming error (never from LLM text).
            EngineSignalSchema.model_validate(esv)
            return explanation, esv  # ✅ success
        except AssertionError as e:
            last_error = str(e)

            # Retry hint appended to query
            clean_query = clean_query + (
                "\n\nIMPORTANT: Follow MODE-2 rules strictly. "
                "Do NOT speculate, invent moves, or mention engine intentions."
            )

    # If all retries failed → return safe fallback
    return (
        "I cannot provide a fully reliable explanation for this position "
        "without violating analysis constraints. Please try another position.",
        esv,
    )
