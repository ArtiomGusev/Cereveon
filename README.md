# Cereveon

**A chess coaching system that explains, but never invents.**

Cereveon decouples the three roles of a chess engine — picking moves, judging
positions, and explaining what's happening — into independently verifiable
layers, then enforces by code that no layer ever crosses into another's job.
The opponent engine never explains. Stockfish never plays. The language model
never calculates.

The result is an AI tutor whose explanations are auditable end-to-end: every
sentence the user reads is a function of a deterministic engine signal, a
rule-based document retrieval, a fixed-order prompt template, and a contract
validator that has the final say. The architecture exists to make
hallucination structurally impossible, not to detect it after the fact.

| | |
|---|---|
| **Status** | Source-available · production deployed |
| **Backend** | Python 3.13 · FastAPI · Stockfish pool · DeepSeek API (LLM) |
| **Client** | Android (Kotlin) · native C++ ~1800 Elo opponent via JNI |
| **Tests** | 1 723 passing · coverage 94.4 % · 95 % floor on validators |
| **License** | See [`docs/LICENSE.md`](docs/LICENSE.md) |

---

## Table of Contents

1. [What this is](#what-this-is)
2. [At a glance](#at-a-glance)
3. [Architecture](#architecture)
4. [SECA — adaptation without retraining](#seca--adaptation-without-retraining)
5. [Quick start](#quick-start)
6. [API](#api)
7. [Configuration](#configuration)
8. [Production deployment](#production-deployment)
9. [Testing](#testing)
10. [Repository structure](#repository-structure)
11. [Security](#security)
12. [Contributing](#contributing)
13. [Further reading](#further-reading)

---

## What this is

Cereveon is a coaching application built around a deliberate constraint:
**explanation is a separate concern from move generation, and must be
treated as untrusted output.** Every other design choice flows from that
premise.

| Cereveon **is** | Cereveon **is not** |
|---|---|
| A non-calculating chess explainer | A chess engine, or a competitor to one |
| A tutor that grounds prose in engine truth | A move-recommendation service |
| A safety-enforced LLM application | A general-purpose chat product |
| Auditable end-to-end | Personalised in ways that bypass the contract |
| Source-available | Open-source (see licence) |

The system runs an Android client against a FastAPI backend; the backend
houses a Stockfish process pool for evaluation, a managed DeepSeek API
client for prose, and the Mode-2 explanation pipeline that gates everything
between them. A small, deterministic adaptation layer (SECA) personalises
opponent strength and teaching tone per player without ever retraining a
model.

---

## At a glance

```
┌────────────────────┐     HTTPS / X-Api-Key + JWT     ┌──────────────────────┐
│ Android client     │ ───────────────────────────────►│ FastAPI server       │
│ • board UI         │                                  │ • Stockfish pool     │
│ • SECA gate poll   │                                  │ • Mode-2 pipeline    │
│ • C++ opponent     │                                  │ • SECA freeze guard  │
│   (JNI, ~1800 Elo) │                                  │ • Auth (JWT + HMAC)  │
└────────────────────┘                                  └────────┬─────────────┘
                                                                 │
                                                  ┌──────────────┼──────────────┐
                                                  ▼              ▼              ▼
                                          ┌──────────────┐ ┌─────────┐ ┌──────────────┐
                                          │ DeepSeek API │ │ Postgres│ │ Redis (opt.) │
                                          │ (managed LLM)│ │ (auth + │ │ (move L2)    │
                                          │              │ │  events)│ │              │
                                          └──────────────┘ └─────────┘ └──────────────┘
```

Two engines, two distinct roles:

- **Opponent engine** — bundled C++, ~1800 Elo, runs on-device through the
  Android JNI bridge. Sole job: pick the opponent's move.
- **Stockfish pool** — server-side, pooled subprocesses. Sole job: produce
  the engine signal (ESV) consumed by the Mode-2 pipeline.

---

## Architecture

The full specification lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
The summary below states the load-bearing rules; details belong in the spec.

### Mode-2 data flow

```
Stockfish JSON  (ground truth)
       ↓
Engine Signal Vector  (deterministic, lossy, no PV / depth / scores)
       ↓
RAG retrieval  (rule-based, no embeddings, no vector DB)
       ↓
Mode-2 prompt rendering  (fixed injection order, golden-tested)
       ↓
LLM generation  (untrusted, only stochastic step)
       ↓
Output validators  (hard gate, never bypassed)
       ↓
Bounded retries  (≤ 2 quality retries, never to recover from a safety violation)
       ↓
Final response
```

![Cereveon Mode-2 pipeline — Android client → API → engine truth → ESV → RAG → prompt → LLM (untrusted) → validators → response](docs/architecture-diagram.svg)

No step may be skipped or reordered. Validators are the only place a final
verdict on output is allowed.

### Architectural invariants

| Invariant | Enforced by |
|---|---|
| Opponent engine never explains | Layer isolation; on-device, never reaches LLM |
| Stockfish never selects opponent moves | Separate pool, separate API surface |
| LLM never calculates or suggests moves | Output validators (hard gate) |
| ESV is the sole engine-derived input downstream | `extract_engine_signal` is the only source |
| Prompt injection order is fixed | Golden snapshot tests on the rendered prompt |
| Explanations only after a move is committed | Pipeline ordering |
| No decision-making component depends on LLM output | Trust boundary in code |

### Trust boundaries

| Component | Trust | Deterministic |
|---|---|---|
| Stockfish JSON | Trusted | ✅ |
| Engine Signal Vector (ESV) | Trusted | ✅ |
| RAG document corpus | Trusted | ✅ |
| Prompt renderer | Trusted | ✅ |
| **LLM output** | **Untrusted** | ❌ |
| Output validators + firewall | Trusted | ✅ |

Non-determinism is isolated to LLM generation. Everything else is
reproducible from inputs.

### Output validators

`llm/rag/validators/` and `llm/rag/safety/output_firewall.py` reject any
LLM response that:

- mentions the engine, depth, search nodes, or principal variations
- suggests, names, or invents chess moves (algebraic notation, castling)
- claims a forced mate the engine didn't see, or omits the inevitability
  acknowledgement when the engine *did* see one
- discloses the system prompt, claims an alternate identity, or leaks PII

A failed validator is a hard stop. The bounded retry mechanism exists only
to improve *quality* of an already-passing response — not to recover from a
safety violation.

When the LLM path fails — validator rejection after the in-pipeline repair
budget (`run_mode_2`'s ≤ `MAX_MODE_2_RETRIES` rewrite attempts is exhausted),
output-firewall block, or LLM unreachable — the request does not return a
patched LLM string. It falls through to a deterministic fallback
(`_build_reply_deterministic` in `llm/seca/coach/chat_pipeline.py`, with the
parallel path in `llm/seca/coach/live_move_pipeline.py`) that builds the
reply from scratch using only trusted inputs: the engine signal, the
`SafeExplainer` output, and a deterministic context block. The LLM's text
is discarded entirely; the fallback never appends to or edits it. By
construction, the reply cannot contain forbidden phrases because none are
ever introduced. This fallback is documented under
[*Deterministic Fallback*](docs/ARCHITECTURE.md#deterministic-fallback) in
the architecture spec, and is forbidden from being widened to accept any
LLM-derived content.

---

## SECA — adaptation without retraining

SECA is the thin adaptation layer under `llm/seca/`: a 5-action
contextual bandit plus a deterministic skill tracker that adapt to
individual players without retraining either base model (Stockfish or
the LLM). Every adaptation step is closed-form
(`A ← A + xxᵀ`, `b ← b + r·x` for the bandit; deterministic deltas for
rating / confidence / weakness / embedding state), and a hard runtime
guard (`llm/seca/safety/freeze.py`) enforces that nothing heavier — no
gradient steps, no neural retraining, no autonomous RL — ever runs in
the live process.

The full specification is in [`docs/SECA.md`](docs/SECA.md). The
load-bearing facts:

| Property | Status in this build |
|---|---|
| Six-step loop (input → action → output → reward → update → repeat) | All steps live |
| Bandit decision head | Closed-form LinUCB (`A ← A + xxᵀ`, `b ← b + r·x`); shadow warm-up by default, user-visible behind `SECA_USE_BANDIT_COACH=1` |
| Online updates to base models (engine, LLM) | **Forbidden** by the freeze guard |
| Background training tasks | **Forbidden** at startup |
| Reward-signal trust | **Engine-derived.** `/game/finish` recomputes accuracy + weaknesses from the submitted PGN via the engine pool before they reach the bandit, skill tracker, or storage. Falls back to client-supplied values only when the engine pool is unavailable; emits `ACC_FALLBACK` / `ACC_DIVERGENCE` telemetry. See [`docs/SECA.md`](docs/SECA.md#trust-property-of-the-reward-signal). |

### Freeze guard

`llm/seca/safety/freeze.py` enforces the no-retraining rule with three
independent checks at startup, plus a per-request structural twin:

1. **Brain-tree allowlist** — anything under `llm.seca.brain.*` not on the
   tiny allowlist (schema modules + `context_builder` + `experience_store`
   + `decision`) is forbidden.
2. **Forbidden module-name parts** — substring matches against historic
   adaptive components (e.g. `brain.rl`, `brain.bandit.online`).
3. **Forbidden source keywords** — substring matches against module
   *source text*: `optimizer.step`, `loss.backward`, `.partial_fit(`,
   `train(`, `bandit.update`, `bandit.save`, `import torch`, `nn.Module`.

A violation `sys.exit(1)`s the process at startup. The per-request
verifier (`verify_runtime_safety`) is wired into `GET /seca/status`
as of 2026-05-14: the endpoint reflects the live runtime, not just
the boot-time constant, so a forbidden brain module lazily loaded
after startup flips the next status response to `safe_mode: false`
without crashing the process.

### Dormant-code policy

Earlier revisions of this codebase carried a substantial volume of dormant
RL/ML code. Five deletion sweeps removed it; what remains under `seca/` is
either live or a deliberately-kept allowlisted stub. Reviving any of the
deleted research is a deliberate act — the relevant module has to be
rewritten, the freeze allowlist updated, determinism guarantees
documented, and tests added that pin the behaviour. The freeze guard's
keyword scan is the re-introduction tripwire.

---

## Quick start

### Docker (recommended)

```bash
cp .env.example .env       # fill in values if you want non-defaults
docker compose up
```

API at `http://localhost:8000`. LLM coaching is provided by the
[DeepSeek API](https://platform.deepseek.com); set your key in `.env`:

```bash
echo 'COACH_DEEPSEEK_API_KEY=sk-...' >> .env
```

Without it, every `/chat` call falls back to the deterministic template
(`chat_pipeline.py:557` — see the trust-boundary diagram). The API still
serves; coaching just degrades to canned responses. `GET /llm/health`
surfaces the live LLM status so you can detect this in monitoring rather
than only in chat replies.

### VS Code dev container

Open the repo and choose **"Reopen in Container"**. Provisions Python 3.13,
Node.js 22, Stockfish, and all Python dependencies. The DeepSeek API key
still needs to be set in `.env` (or as a shell env var).

### Bare-metal Python

```bash
sudo apt install stockfish      # or: brew install stockfish
pip install -r llm/requirements.txt
cp .env.example .env
python -m uvicorn llm.server:app --host 0.0.0.0 --port 8000
```

Stockfish is auto-detected from `PATH`, falling back to
`/usr/games/stockfish` (Linux) or `engines/stockfish.exe` (Windows).
Override with `STOCKFISH_PATH`.

### Android

`android/local.properties` is gitignored and machine-specific:

```bash
# macOS / Linux / WSL
./scripts/setup-android.sh

# Windows (PowerShell)
"sdk.dir=$($env:LOCALAPPDATA -replace '\\','/')/Android/Sdk" > android\local.properties
```

Or open `android/` in Android Studio — it generates the file
automatically. The app builds for `arm64-v8a` (physical devices, Apple
Silicon emulators) and `x86_64` (Intel/AMD AVDs).

---

## API

The backend exposes a FastAPI application at `llm/server.py`. The
authoritative schema reference is
[`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md); the table below lists
the endpoint surface.

### Authentication

Two layers, both enforced server-side:

1. **API key** — `X-Api-Key: <SECA_API_KEY>` header. Required on coaching
   endpoints. Bypassed in `SECA_ENV=dev` *only* when
   `SECA_INSECURE_DEV=true`; defaults to enforced in dev too.
2. **JWT session tokens** — issued by `/auth/register` and `/auth/login`
   for player sessions. `Authorization: Bearer <token>`. Sliding refresh
   via the `X-Auth-Token` rotation header.

Constant-time comparison (`hmac.compare_digest`) is used for the API-key
check. A missing `SECA_API_KEY` or short `SECRET_KEY` (< 32 chars) crashes
the server at startup when `SECA_ENV=prod` — by design.

### Endpoint catalogue

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| GET | `/` | — | — | Liveness probe |
| GET | `/health` | — | — | Health check |
| GET | `/seca/status` | — | — | Per-request SECA verification (safety gate) |
| GET | `/debug/engine` | API key | — | Engine pool depth |
| POST | `/live/move` | JWT | 30/min | Real-time coaching hint on player move |
| POST | `/explain` | API key | — | Deterministic SAFE_V1 explanation (no LLM) — see note below |
| POST | `/chat` | API key | 10/min | Multi-turn coaching conversation |
| POST | `/chat/stream` | API key | 10/min | SSE-streamed chat |
| GET | `/next-training/{player_id}` | JWT | — | Curriculum next-task recommendation |
| POST | `/game/start` | JWT | — | Open a game record |
| POST | `/game/{id}/checkpoint` | JWT | — | Cross-device resume checkpoint |
| GET | `/game/active` | JWT | — | Active game for this player |
| GET | `/repertoire` | JWT | — | Player repertoire (ECO list) |
| POST | `/repertoire` | JWT | — | Add an opening |
| DELETE | `/repertoire/{eco}` | JWT | — | Remove an opening |
| POST | `/repertoire/{eco}/drill-result` | JWT | — | Record a drill outcome |
| POST | `/repertoire/{eco}/active` | JWT | — | Set the active opening |
| POST | `/auth/register` | — | — | Create account |
| POST | `/auth/login` | — | — | Issue JWT |
| POST | `/auth/logout` | JWT | — | Invalidate session |
| GET | `/auth/me` | JWT | — | Current player profile |
| PATCH | `/auth/me` | JWT | — | Update profile |
| POST | `/auth/change-password` | JWT | — | Rotate password |
| POST | `/game/finish` | JWT | — | Close a game; runs SECA loop |
| POST | `/game/coach-feedback` | JWT | — | Per-game coach feedback |
| GET | `/game/history` | JWT | — | Recent games |

> **`/explain` and `/seca/explain` are intentionally deterministic.** Both
> routes run the engine signal through `SafeExplainer` and return prose
> templated from the ESV bands — no LLM call, no RAG retrieval, no
> contract validators (the templates are safe by construction). The
> real Mode-2 LLM pipeline (RAG retrieval → prompt rendering → DeepSeek
> → output validators → deterministic fallback) is reachable via
> `/chat` and `/chat/stream`. Keeping the `/explain` routes
> deterministic means they are free, fast, and CI-friendly; the Android
> client routes coaching conversation through `/chat` and uses
> `/explain` for cheap structured explanations only.

### API schema versioning

Two response headers carry the version contract:

| Header | Value | Meaning |
|---|---|---|
| `X-API-Version` | `2` (currently) | Server's *preferred* version — what new clients should target |
| `X-API-Versions-Supported` | `1, 2` (comma-separated when multiple) | Every version the server will accept on inbound requests |

The Android client sends `X-API-Version` on coaching requests; the
server gates on it (**lenient on missing**, **strict on
unsupported**):

| Client header | Server response |
|---|---|
| absent | request proceeds; INFO log records the missing-header request |
| listed in `X-API-Versions-Supported` | request proceeds silently |
| anything else | HTTP 400 with `{"detail": "..."}` naming the supported range |

Discovery routes (`/`, `/health`, `/seca/status`) never reject on
mismatch so an out-of-date client can still read both response
headers and surface an "update the app" UI. CORS preflights explicitly
allow `X-API-Version`; CORS `expose_headers` includes both response
headers so browser scripts can read them.

#### Bumping the version

Two distinct flows, depending on whether the new version is
breaking or additive:

- **Additive (back-compat).** Append the new version to
  `API_VERSIONS_SUPPORTED` in `llm/server.py` (and update
  `API_VERSION` to the new value if it becomes the preferred).
  Existing clients keep working — they're still in the supported
  list. No coordinated Android release required.
- **Breaking (drop old version).** Same as above, then drop the
  old version from `API_VERSIONS_SUPPORTED` **after** the Android
  client has rolled out to a sufficient fraction of users. The
  bump from supported set `(1, 2)` to `(2,)` is the breaking
  release; the migration window (when both are supported) is the
  grace period.

In either case, also update `COACH_API_VERSION` in
`android/app/src/main/java/ai/chesscoach/app/ApiVersion.kt` when
the *preferred* version bumps so new Android builds advertise
the new version. Pinned by
`llm/tests/test_api_version_header.py` (AVH_01..AVH_14) +
`llm/tests/test_doc_constants_pinned.py::test_api_version_constant`.

### Security response headers

Every response carries:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`

CORS is configured via `CORS_ALLOWED_ORIGINS`. If unset, all cross-origin
requests are blocked with a startup warning. Request bodies are capped at
512 KB at the FastAPI middleware layer.

---

## Configuration

All configuration is via environment variables. `.env.example` is the
authoritative reference; the table below covers the variables operators
need most often.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `SECA_API_KEY` | `dev-key` | Auth key. Any value works in `dev`; required in `prod`. |
| `SECA_ENV` | `dev` | `dev` or `prod`. |
| `SECRET_KEY` | — | JWT signing secret (≥ 32 chars; required in `prod`). |
| `COACH_DEEPSEEK_API_KEY` | — | **Required for LLM coaching**. Sign up at [platform.deepseek.com](https://platform.deepseek.com), create a key, paste here. Without it the api still serves but every `/chat` call falls back to the deterministic template. |
| `COACH_DEEPSEEK_API_BASE` | `https://api.deepseek.com` | OpenAI-compatible endpoint. Override only when pointing at a self-hosted gateway (LiteLLM, vLLM, etc.). |
| `COACH_DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek-V4 non-thinking (successor to the retired `deepseek-chat`); ~$0.14/M input + $0.28/M output. Alternative `deepseek-v4-pro` (thinking) costs more — usually overkill for explain-the-position prose. |
| `STOCKFISH_PATH` | auto-detected | Override Stockfish binary path. |
| `REDIS_URL` | *(unset)* | Redis URL for the L2 move cache; in-memory only when unset. |
| `DATABASE_URL` | `sqlite:///data/seca.db` | SQLAlchemy DSN. PostgreSQL required for multi-worker deployments. |
| `CORS_ALLOWED_ORIGINS` | *(empty — blocks all cross-origin)* | Comma-separated allowed origins. |
| `TRUSTED_PROXIES` | prod: empty (warning logged); dev: `127.0.0.0/8, ::1` | Reverse-proxy IPs / CIDRs. **Required in prod** for per-client rate limiting; otherwise every request behind the reverse proxy keys on the same bucket. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) > Trusted Proxies. |
| `SECA_INSECURE_DEV` | *(unset → `false`)* | Local-development opt-in for the no-`SECA_API_KEY` auth bypass. **Never set in production** — see [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) § T6. |
| `ENGINE_POOL_SIZE` | `8` | Concurrent Stockfish processes. |
| `ENGINE_THREADS` | `1` | Threads per Stockfish process. |
| `ENGINE_HASH_MB` | `128` | Hash table per process (MB). |
| `ENGINE_SKILL_LEVEL` | `10` | UCI skill level. |
| `ENGINE_DEFAULT_MOVETIME_MS` | `40` | Default search time. |
| `ENGINE_ASYNC_PREDICT_ENABLED` | `true` | Predictive move pre-caching. |
| `ENGINE_CACHE_VERSION` | *(unset)* | Manual cache flush override. The cache key already fingerprints the engine config, so this is only needed when the Stockfish binary is replaced in place at the same path. |
| `SECA_USE_BANDIT_COACH` | `false` | When `true`, the LinUCB bandit's selection becomes user-visible in `/game/finish`. Default: shadow warm-up only. |

### Common issues

| Symptom | Resolution |
|---|---|
| `UnsatisfiedLinkError: libchessengine.so` | Expected on host JVM; `NativeEngineProvider` returns null gracefully. Run on device/emulator via `connectedAndroidTest`. |
| `FileNotFoundError: stockfish` | Install Stockfish or set `STOCKFISH_PATH` in `.env`. |
| `host.docker.internal` unreachable on Linux | `docker-compose.yml` adds `host-gateway` automatically. For bare `docker run`, add `--add-host=host.docker.internal:host-gateway`. |
| `sdk.dir` Gradle error | Run `./scripts/setup-android.sh` or open `android/` in Android Studio. |
| Server refuses to start in prod | `SECA_API_KEY` and `SECRET_KEY` must be set when `SECA_ENV=prod`. |
| Rate limits feel "stuck on one bucket" | `TRUSTED_PROXIES` is unset in prod; the limiter keys on Caddy's IP for every request. Set the variable to the proxy CIDR. |

---

## Production deployment

Production runs a **single tier**, built and signed by the CI pipeline.
Full runbook: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

| Tier | Image | Source | Port | Role |
|---|---|---|---|---|
| **Hetzner backend** | `cereveon-llm-api` | [`llm/Dockerfile.api`](llm/Dockerfile.api) (Python + Stockfish + full SECA stack) | 8000 | Everything: public ingress at `cereveon.com` behind Caddy, engine pool, RAG, validators, auth, Postgres + Redis. LLM coaching via DeepSeek API. |

Auto-deploys from
[`.github/workflows/fly-deploy.yml`](.github/workflows/fly-deploy.yml) on
push to `main`: the `deploy` job runs the zero-downtime rolling swap on
Hetzner (scale=2, health-gate, drain-or-rollback). The filename is
historical; the workflow's `name:` field is `CI/CD`, which is
authoritative.

> A former Fly.io Node edge (`llm/server.js`, an Ollama prototype) was
> removed 2026-07-15 — it never fronted `cereveon.com` (that resolves to
> Hetzner's Caddy directly) and served only an unauthenticated,
> validator-bypassing `/coach`·`/explain`. See the runbook for the
> `flyctl apps destroy chesscoach` teardown step.

### Container hardening

`docker-compose.prod.yml` ships two hardening tiers, applied per
service. The aggressive tier (`api`, `redis`) carries `read_only: true`,
`tmpfs: [/tmp]`, `cap_drop: [ALL]`, and `no-new-privileges`. The
conservative tier (`caddy`, `db`) carries only
`no-new-privileges` pending staging validation of upstream-specific
hardening recipes. Pinned by `llm/tests/test_container_hardening.py`
(CH_01–CH_13). See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) §8 for
the full rationale.

### Release process

Releases follow `vMAJOR.MINOR.PATCH`:

- **MAJOR** — architectural or contract changes
- **MINOR** — new features, RAG documents, golden cases
- **PATCH** — bug fixes, wording improvements, no behavior change

Pre-release checklist (non-negotiable):

1. Clean working tree (`git status`)
2. CI-safe tests pass (golden, contract, API contract, pipeline regression)
3. LLM regression tests pass: `RUN_DEEPSEEK_TESTS=1 COACH_DEEPSEEK_API_KEY=... pytest llm/rag/tests/llm/test_llm_regression.py`
4. Real-LLM smoke test passes: `RUN_DEEPSEEK_TESTS=1 COACH_DEEPSEEK_API_KEY=... pytest llm/rag/tests/llm/test_deepseek_smoke.py`
5. Manual output sanity review (no engine mentions, no move suggestions)

Pushing a `vX.Y.Z` tag publishes the GitHub Release and GHCR images for
both `cereveon:vX.Y.Z` and `cereveon-llm-api:vX.Y.Z`.

---

## Testing

The project uses six test categories. No layer is unprotected.

| Category | Scope | CI? | Command |
|---|---|---|---|
| **A — Golden** | ESV mapping, RAG retrieval, prompt snapshots | ✅ | `pytest llm/rag/tests/golden/` |
| **B — Contract** | Forbidden patterns, mate handling, missing data (Fake LLM) | ✅ | `pytest llm/rag/tests/contracts/` |
| **C — Smoke** | DeepSeek API connectivity, output passes validators | local + tag pushes (gated on `COACH_DEEPSEEK_API_KEY`) | `RUN_DEEPSEEK_TESTS=1 pytest llm/rag/tests/llm/test_deepseek_smoke.py` |
| **D — Regression** | Repeated real-LLM runs, contract compliance over time | tag pushes + weekly cron (gated on `COACH_DEEPSEEK_API_KEY`) | `RUN_DEEPSEEK_TESTS=1 pytest llm/rag/tests/llm/test_llm_regression.py` |
| **E — Quality** | Length, sentence structure, non-triviality | advisory | `pytest llm/rag/tests/quality/` |
| **F — Mutation** | mutmut against `llm/rag/validators/`: does the test fail when the validator is logically wrong? | local, on-demand | `bash scripts/run_mutation_tests.sh` |

The **Fake LLM** is mandatory: it simulates contract violations to prove
validator enforcement, and is not optional.

### Quality gates (precise, not vague)

"Mypy passes" and "Pylint passes" are commitments only as strict as the
config behind them. The actual scope and rule set:

- **Black** — `py313`, line length `100`. Scope: 22-file whitelist in
  `llm/run_quality_gate.py:FORMAT_TARGETS`.
- **Pylint** — default rule set MINUS the permissive softeners in
  `pyproject.toml [tool.pylint."messages control"] disable`
  (`broad-exception-caught`, `line-too-long`, missing-docstring trio,
  `subprocess-run-check`, `too-few-public-methods`, the `too-many-*`
  trio). Every other category is enforced.
- **Mypy** — 16-file `MYPY_TARGETS`. `python_version = "3.13"`,
  `ignore_missing_imports = true`. Trust-boundary modules
  (`llm.rag.validators.*`, `llm.rag.safety.*`, `llm.rag.contracts.*`)
  carry stricter overrides: `disallow_untyped_defs`, `check_untyped_defs`,
  `disallow_incomplete_defs`, `warn_return_any`.
- **Coverage** — global ≥ 80 %, validators (`llm/rag/validators/*.py`)
  and the post-LLM safety firewall (`llm/rag/safety/*.py`) ≥ 95 %.
  Per-module floors enforced by `llm/check_coverage_thresholds.py`.
- **`pip-audit`** — strict mode against `llm/requirements.txt` and
  `llm/requirements-ci.txt`. Any unfixed CVE blocks the merge.
- **Trivy** — image scan on the published GHCR images. `CRITICAL`
  unfixed → block; `HIGH/CRITICAL` → SARIF upload to the Security tab.

### Hardening tripwires

- **`python -O` regression** — validators on the production path use
  explicit `if not <cond>: raise AssertionError(...)` rather than bare
  `assert`. Pinned by
  `llm/rag/tests/unit/test_validator_dash_o_hardening.py`
  (`VAL_DASH_O_01`); see the policy block in `pyproject.toml`.
- **Cross-tenant 404 collapse** — path-id endpoints return the same 404 +
  `"Not Found"` body FastAPI emits for an unmounted URL, removing the
  enumeration oracle. Pinned by
  `test_security_authz.py::TestAut01CrossTenantNoLeak`.
- **Stockfish crash recovery** — a crashed child is detected at release
  time and replaced with a fresh `_spawn_engine()`; pool size survives
  the crash. Pinned by `test_engine_pool_crash_recovery.py` (CR_01–CR_08).
- **Proxy-aware rate limiting** — `TRUSTED_PROXIES` walks XFF
  right-to-left; spoof attempts cannot escape an IP's bucket. Pinned by
  `test_security_proxy_aware_limiter.py` (TPA_01–TPA_14).

### Running tests

```bash
python llm/run_ci_suite.py                       # full CI suite (1 723 tests)
python llm/run_quality_gate.py black             # formatting
python llm/run_quality_gate.py pylint            # linting
python llm/run_quality_gate.py mypy              # types
cd android && ./gradlew test                     # Android host JVM tests
cd android && ./gradlew connectedAndroidTest     # Android instrumented tests
```

Real-LLM regression tests (Category D) must run before every release,
after any system-prompt or RAG-document change, and after any model
update — and never in CI.

---

## Repository structure

```
.
├── android/                  # Kotlin client (UI, game orchestration, JNI)
├── engine/                   # C++ opponent engine (~1800 Elo, JNI bridge)
├── llm/
│   ├── server.py             # FastAPI entry point — also hosts /engine/eval
│   ├── explain_pipeline.py   # Mode-2 outer pipeline + bounded retries
│   ├── rag/
│   │   ├── engine_signal/    # ESV extraction
│   │   ├── retriever/        # Deterministic rule-based retrieval
│   │   ├── prompts/          # Mode-2 prompt templates (golden-tested)
│   │   ├── validators/       # Output contracts (≥ 95 % coverage floor)
│   │   ├── safety/           # Output firewall (≥ 95 % coverage floor)
│   │   ├── llm/              # BaseLLM + Fake adapter (real LLM via call_llm → DeepSeek)
│   │   ├── llm/run_mode_2.py # Inner repair loop + REQUIRED-phrase fail-safe
│   │   ├── deploy/embedded.py
│   │   ├── documents/        # Static RAG corpus (no runtime submission)
│   │   └── tests/            # Golden, contract, regression, quality, mutation
│   ├── seca/
│   │   ├── safety/freeze.py  # Startup + per-request safety enforcement
│   │   ├── auth/             # JWT issuance + sliding refresh
│   │   ├── brain/bandit/     # Allowlisted: context_builder, decision (LinUCB), experience_store
│   │   ├── learning/         # Outcome tracking, skill update, player embedding
│   │   ├── world_model/      # SafeWorldModel stub only
│   │   ├── coach/            # Chat + live-move pipelines
│   │   ├── curriculum/       # Live curriculum router + scheduler
│   │   ├── adaptation/       # Per-session ELO drift, teaching policy
│   │   ├── analytics/        # Event logging + training recommendations
│   │   ├── analysis/         # Read-only historical roll-up
│   │   ├── events/           # /game/finish event handling
│   │   ├── storage/          # SQLAlchemy + raw-SQL split
│   │   ├── engines/stockfish/ # Live engine pool
│   │   ├── shared_limiter.py # Proxy-aware rate-limit key
│   │   └── runtime/safe_mode.py
│   └── tests/                # API contracts, security, hardening, integration
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SECA.md
│   ├── API_CONTRACTS.md
│   ├── TESTING.md
│   ├── THREAT_MODEL.md
│   ├── DEPLOYMENT.md
│   ├── OPERATIONS.md
│   ├── OPERATIONS_RETRIES.md
│   ├── RELEASE.md
│   └── LICENSE.md
├── design/                   # React/Babel design canvas (not part of build)
├── scripts/                  # Operational helpers (smoke test, mutation runner, …)
├── docker-compose.yml        # Dev compose stack
├── docker-compose.prod.yml   # Prod compose stack with container hardening
└── pyproject.toml            # Black / Mypy / Pylint / pytest config
```

---

## Security

The full threat model lives in
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md). Key controls:

| Threat | Control |
|---|---|
| Prompt injection via `user_query` | Pre-LLM sanitiser → structured prompt rendering → Mode-2 contracts → output firewall → bounded retries |
| JWT replay / session theft | HMAC-signed tokens, ≥ 32-char secret, sliding refresh, HTTPS-only, per-IP rate limit |
| Engine-pool DoS | Bounded pool, fast-fail queue timeout, movetime ceiling, L1+L2 cache, slowapi per-IP, transport-liveness probe on release |
| Malicious RAG document | No runtime submission surface; corpus is static in-tree, golden-pinned |
| Operator misconfiguration | Two-flag bypass (`SECA_INSECURE_DEV`); hard startup guards on `SECA_API_KEY` and `SECRET_KEY` in prod; CORS defaults closed |

Cross-cutting: constant-time secret compare, security response headers,
512 KB body cap, container hardening, per-request SECA verification.

The audit history of past findings — what was open, what's closed,
where in the code — is tracked in
`memory/project_security_depth_audit.md` (kept out of the public docs
tree intentionally).

---

## Contributing

The architecture is the contract. Changes are categorised by what they
touch.

### Allowed without architectural review

- Add a RAG document to `llm/rag/documents/`
- Add a golden test case
- Tighten an existing validator
- Improve explanation wording within existing contracts
- Add a new `BaseLLM` adapter

### Requires architectural review

- New endpoint (extend `THREAT_MODEL.md` §3 with a fresh threat row)
- Auth path other than JWT or `X-Api-Key`
- Output firewall category change
- New telemetry field
- Change to the `extend-exclude` or quality-gate target lists in
  `pyproject.toml` and `llm/run_quality_gate.py`
- Change to the SECA freeze allowlist (`ALLOWED_BRAIN_MODULES`,
  `FORBIDDEN_KEYWORDS`)
- New env variable that affects production behaviour

### Forbidden

- Weakening output validators
- Bypassing or replacing the ESV
- Dynamic prompt mutation at runtime
- LLM reasoning beyond provided inputs
- Autonomous RL implementation
- Disabling or skipping SECA enforcement
- Skipping `--no-verify` on commits
- Force-pushing to `main`

The repo's [`CLAUDE.md`](CLAUDE.md) lists the same rules in
machine-actionable form for AI assistants working in the tree.

---

## Further reading

| Doc | Purpose |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Formal system specification, trust boundaries, data flow |
| [`docs/SECA.md`](docs/SECA.md) | Self-Evolving Coaching Architecture: framework, six-step loop, freeze guard, dormant-code policy |
| [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) | Authoritative endpoint schemas |
| [`docs/TESTING.md`](docs/TESTING.md) | Test categories, validator coverage matrix, quality gates |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | Adversaries, threats, mitigations, accepted residuals |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Production runbook, topology, container hardening |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Runtime monitoring, telemetry, incident response |
| [`docs/OPERATIONS_RETRIES.md`](docs/OPERATIONS_RETRIES.md) | Bounded retry policy and telemetry interpretation |
| [`docs/RELEASE.md`](docs/RELEASE.md) | Mandatory release procedure and invariants |
| [`docs/LICENSE.md`](docs/LICENSE.md) | Source-available licence terms |

---

## Design philosophy

Cereveon prioritises:

1. **Correctness** — invariants enforced via code and tests, not convention
2. **Determinism** — every layer except LLM generation is reproducible
3. **Non-hallucination** — ESV normalisation + output validators make engine fabrication structurally impossible
4. **Safety** — strict contracts on every LLM output; no output is always better than unsafe output
5. **Maintainability** — loose coupling with explicit trust boundaries at every layer

Over convenience, feature velocity, and explanation quality at the
expense of correctness.

> *No output is always better than unsafe output. If the system refuses
> to respond, that is a success condition, not a failure.*
> — `docs/OPERATIONS.md` §12
