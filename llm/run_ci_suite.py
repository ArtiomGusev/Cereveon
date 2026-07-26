"""Run the stable CI pytest suite with coverage and artifacts."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = PROJECT_ROOT / "tmp_logs"

TEST_TARGETS = [
    "llm/rag/tests/golden/test_retriever.py",
    "llm/rag/tests/golden/test_prompt_snapshot.py",
    "llm/rag/tests/contracts/test_fake_llm.py",
    "llm/rag/tests/contracts/test_mode_2_output.py",
    "llm/rag/tests/contracts/test_violations_corpus.py",
    "llm/rag/tests/test_output_firewall.py",
    # Validator pattern-coverage tests (Category F closure pass): every
    # forbidden-pattern literal is now load-bearing for at least one
    # parametrised case.  Closes the bulk of the 2026-05-07 mutmut
    # baseline survivor set.
    "llm/rag/tests/unit/test_validator_pattern_coverage.py",
    # python -O hardening regression — pins the if/raise pattern in
    # validate_mode_2_negative against future contributors swapping it
    # back to a bare ``assert`` (see [tool.pylint] policy in pyproject).
    "llm/rag/tests/unit/test_validator_dash_o_hardening.py",
    "llm/rag/tests/test_run_mode_2_additional.py",
    "llm/rag/tests/test_run_mode_2_cascades.py",
    "llm/rag/tests/test_run_mode_2_mate_sanitization.py",
    "llm/rag/tests/test_explanation_score.py",
    "llm/rag/tests/unit/test_input_sanitizer.py",
    "llm/tests/test_engine_response_format.py",
    "llm/rag/tests/unit/test_telemetry_event.py",
    "llm/tests/test_cache_keys.py",
    "llm/tests/test_ci_pipeline.py",
    "llm/tests/test_fen_move_cache_key.py",
    "llm/tests/test_position_input_build_board.py",
    "llm/tests/test_stockfish_adapter_isolation.py",
    "llm/tests/test_seca_layer_boundaries.py",
    "llm/tests/test_coaching_pipeline_regression.py",
    "llm/tests/test_api_contract_validation.py",
    "llm/tests/test_explain_schema_validation.py",
    "llm/tests/test_mistake_analytics.py",
    "llm/tests/test_chat_pipeline.py",
    "llm/tests/test_chat_stream.py",
    # Freemium chat-quota 402 gate on /chat + /chat/stream (endpoint tier):
    # 3-turn free cap → documented 402 body, failed pipeline eats no turn,
    # and the stream route consumes at the terminal event via a fresh
    # session.  "Tests exist but never run" debt (same class as
    # test_per_game_chat_alignment / test_lichess_import): the file was
    # never registered, and its _ChatPipelineSpy had silently drifted out
    # of sync with generate_chat_reply's arity (player_color) so ALL of it
    # 500'd — invisible because it wasn't in this list.  Registered +
    # repaired 2026-07-15.
    "llm/tests/test_chat_entitlements_402.py",
    # RevenueCat webhook — the subscription source of truth: auth
    # (503 unconfigured / 401 mismatch), grant/revoke event mapping,
    # CANCELLATION-is-no-op, unknown-player/anonymous robustness, and
    # idempotency.  Registered on creation so it does NOT join the
    # silent-skip debt above.
    "llm/tests/test_revenuecat_webhook.py",
    # Cereveon app-help layer: detector precision/recall, prompt injection
    # wiring, and guide-text safety (no forbidden output / no engine
    # reveal / no unimplemented-feature claims).
    "llm/tests/test_app_help.py",
    # Prompt-cache prefix invariants (PCP_01..03): the static system
    # prompt + app guide stay a byte-identical request prefix, and
    # history extends the cached prefix within a conversation — the
    # layout DeepSeek's automatic context cache bills at ~1/50th rate.
    "llm/tests/test_prompt_cache_prefix.py",
    "llm/tests/test_live_move_pipeline.py",
    # Mode-1 player-perspective + prompt-grounding pins.  Was never in
    # this list — a latent assertion failure (instruction-quote false
    # positive in test_transient_check_fact_is_suppressed) sat invisible
    # on main until the 2026-07-10 JSON-compaction work ran the file
    # directly (4th instance of the silent-skip drift class).
    "llm/tests/test_mode1_player_perspective.py",
    "llm/tests/test_historical_pipeline.py",
    "llm/tests/test_api_security.py",
    "llm/tests/test_skill_updater_resilience.py",
    "llm/tests/test_game_finish_db_integration.py",
    # GET /game/history provenance filtering (GH_01..08): the `source`
    # field + `?source=app|lichess` filter + bounded `?limit=` that back
    # the Android All / In-app / Lichess history tabs.
    "llm/tests/test_game_history_source.py",
    # Per-game chat alignment (ALIGN_*): calls game_history() directly, so
    # it also guards that the endpoint's Query params keep plain runtime
    # defaults (the Annotated form) — a regression to `= Query(...)` would
    # raise on `.limit(FieldInfo)` here.  Previously unregistered ("tests
    # exist but never run" debt); wired in 2026-07-03.
    "llm/tests/test_per_game_chat_alignment.py",
    "llm/tests/test_engine_pool_exhaustion.py",
    "llm/tests/test_cache_redis_unavailable.py",
    "llm/tests/test_seca_status.py",
    "llm/tests/test_full_loop_integration.py",
    "llm/tests/test_curriculum_next_contract.py",
    "llm/tests/test_security_hardening.py",
    # Authorization-layer security (AUT-01..AUT-02) — JWT cross-tenant
    # check on /next-training, dead-router non-inclusion, AUT-01d/e
    # 404 collapse that closes the player-id enumeration oracle.
    "llm/tests/test_security_authz.py",
    # Trusted-proxy-aware rate limiter (TPA_01..TPA_14) — pins that
    # ``proxy_aware_remote_address`` in ``llm/seca/shared_limiter.py``
    # walks X-Forwarded-For right-to-left honouring TRUSTED_PROXIES,
    # so that per-client rate limits behind Caddy actually key on the
    # real client IP instead of collapsing onto the proxy's container
    # peer.  Regression guard for the wiring is TPA_14.
    "llm/tests/test_security_proxy_aware_limiter.py",
    # Host-header poisoning (BADHOST_01..08) — pins the starlette "BadHost"
    # fix (CVE-2026-48710 / PYSEC-2026-161): a malformed Host header can no
    # longer poison request.url.path, so the path-allow-list exemption in
    # api_version_gate (and the /metrics filter) cannot be bypassed.  RED on
    # the previously-pinned starlette 0.50.0, GREEN on the 1.2.1 now pinned;
    # BADHOST_08 fails CI if starlette is ever re-pinned below 1.0.1.
    "llm/tests/test_security_host_header_poisoning.py",
    # Container hardening floor (CH_01..CH_13) — pins the
    # docker-compose.prod.yml runtime-sandbox contract: api+redis carry
    # read_only / tmpfs / cap_drop ALL / no-new-privileges; caddy+db
    # carry no-new-privileges (conservative tier pending per-upstream
    # validation, see docs/DEPLOYMENT.md > Container Hardening).
    # CH_12 (ollama) is skipped while the prod stack uses the managed
    # DeepSeek API rather than a local Ollama sidecar.
    "llm/tests/test_container_hardening.py",
    # X-API-Version middleware (AVH_01..AVH_10) — pins the schema-version
    # gate on the HTTP boundary: every response carries X-API-Version,
    # coaching paths reject mismatched headers with 400 + both versions
    # named in the detail, discovery routes (/, /health, /seca/status)
    # are exempt so an out-of-date client can still discover the server
    # version, and CORS allow_headers includes the custom header.
    "llm/tests/test_api_version_header.py",
    # Prometheus /metrics endpoint + HTTP request middleware (MET_01..MET_10) —
    # pins the auth shape (X-Api-Key OR Bearer SECA_API_KEY), exposition
    # content-type, expected metric names, and the wire-up that
    # increments chesscoach_http_requests_total / auth_login_total
    # on real requests.  Closes Sprint 5.D.1 observability scope.
    "llm/tests/test_metrics_endpoint.py",
    # LLM + hardware observability (LLM_MET_01..10, HW_MET_01..05,
    # GID_01..05, DASH_01..07) — pins the new metric surface added
    # alongside the LLM + hardware monitoring dashboard:
    #   - chesscoach_llm_request_duration_seconds / tokens / cost / errors
    #   - chesscoach_cpu_percent / memory_percent / memory_used_bytes /
    #     disk_percent / load_avg_1m (psutil-backed gauges)
    #   - game_id contextvar that attributes LLM telemetry to a match
    #   - Grafana dashboard JSON schema (monitoring/dashboards/llm_hardware.json)
    "llm/tests/test_llm_metrics.py",
    "llm/tests/test_hardware_metrics.py",
    "llm/tests/test_game_id_contextvar.py",
    "llm/tests/test_grafana_dashboard_json.py",
    # Structured JSON logging + request_id contextvar propagation
    # (LOG_01..LOG_12) — pins the JSON schema, the SECA_ENV /
    # COACH_LOG_JSON gating, the X-Request-ID echo + validation
    # (length cap, ASCII), the contextvar wiring into LogRecord, and
    # the request-end log line carrying method/path/status/latency_ms.
    # Closes Sprint 5.D.2 observability scope.
    "llm/tests/test_log_config.py",
    # Engine pool crash recovery (CR_01..CR_08) — pins that a Stockfish
    # subprocess crash is detected at release time and the dead handle
    # replaced with a fresh engine.  Without this, a crash mid-request
    # leaves a corpse in the pool's queue and cascades second-order
    # failures across the next pool_size requests.  Fake engines only;
    # no Stockfish binary required.
    "llm/tests/test_engine_pool_crash_recovery.py",
    # Player adaptation regression suite (full-scale adaptation: /move session auth,
    # curriculum history-driven topic, game finish recommendations, SAFE_MODE gate).
    "llm/tests/test_adaptive_player.py",
    # Auth service and token contract tests (layer-correct: service raises ValueError,
    # router converts to HTTP; JWT creation/expiry/tamper coverage).  The
    # rotation / refresh / sliding / missing-header siblings are added below so
    # the Sprint 5.C (per-token revocation) + 5.D (request_id middleware) work
    # contributes to the per-module coverage floor on auth/service.py and
    # auth/router.py — without these listings the suite still passed but their
    # lines didn't show up under --cov.
    "llm/tests/test_auth_service.py",
    "llm/tests/test_auth_tokens.py",
    "llm/tests/test_auth_rotation_regression.py",
    "llm/tests/test_auth_refresh_header.py",
    "llm/tests/test_auth_sliding_session.py",
    "llm/tests/test_auth_missing_header.py",
    # DELETE /auth/me account erasure (GDPR Art. 17, AD_01..12):
    # metadata-driven discovery of every player-linked table pinned
    # against the explicit erasure plan (drift tripwire), two-player
    # purge with bystander isolation, endpoint + dead-token 401,
    # Lichess-account path, the ondelete=CASCADE model pin, and the
    # Postgres FK retrofit's SQLite no-op + DDL shape.
    "llm/tests/test_auth_account_deletion.py",
    # GET /auth/me/export data export (GDPR Art. 15/20, EX_01..09):
    # scope parity with the erasure plan (shared player_data_plan
    # authority), secrets policy incl. the secret-pattern guard over
    # the player-linked closure, cross-player isolation, end-to-end
    # JSON-serialisability, and the route handler's document shape.
    "llm/tests/test_auth_data_export.py",
    # Public web account-deletion page (GDPR Art. 17 + Google Play,
    # WD_01..12): the /delete-account* surface reachable WITHOUT the app.
    # Password path (verify-then-purge + wrong-password / unknown-email /
    # no-confirm guards) and the Lichess web-OAuth flow (PKCE + signed-
    # cookie CSRF state, match-then-purge, no-linked-account), plus the
    # reused exchange redirect_uri override and verify_credentials-issues-
    # no-session pins.
    "llm/tests/test_web_account_deletion.py",
    # Public privacy policy (GET /privacy) — the live URL Google Play
    # requires (PP_01..04).  Load-bearing: the anti-leak guard structurally
    # prevents ever publishing the internal docs/PRIVACY_POLICY.md draft
    # (no placeholder / annex / draft markers may reach the page).
    "llm/tests/test_privacy_policy_page.py",
    # Model-registration completeness (MR_01..05): the glob-driven pin
    # that every table-defining llm/seca/**/models.py is wildcard-
    # imported by auth/router.py BEFORE create_all, plus a full-app
    # (lifespan) register→export→delete→401 journey.  Regression guard
    # for the 2026-07-17 prod incident (brain.training.models missing
    # from the block → "no such table: training_outcomes" 500s on the
    # GDPR endpoints while every fixture-based unit test stayed green).
    "llm/tests/test_model_registration.py",
    # "Sign in with Lichess" (OAuth PKCE, POST /auth/lichess): client-layer
    # exchange/account/revoke error taxonomy + hostile-input rejection
    # (OA_01..08) and router/service find-or-create, auto-link, and
    # synthetic-email squat guards (LI_01..13).  Listed so the new
    # login_with_lichess / _issue_session lines count toward the
    # auth/service.py + auth/router.py per-module coverage floors.
    "llm/tests/test_auth_lichess.py",
    # Lichess ingestion adapter (CL/IS/RT cases: client error taxonomy,
    # link/unlink/import service semantics, router translation).  The file
    # predates the OAuth sign-in work but was never registered in any CI
    # list — "tests exist but never run" debt surfaced by the 2026-07-02
    # sign-in review.  Registered alongside the OAuth suite since /auth/
    # lichess now leans on link_account + the shared client error types.
    "llm/tests/test_lichess_import.py",
    # Lichess post-import engine analysis (AN_00..12 + PL_01): the
    # bounded pass that scores imported games with engine-truth accuracy
    # + weakness vectors so the historical-analysis pipeline consumes
    # them, the producer-side ORM-default pin behind its unscored
    # predicate, its run_import_job seams (no-pool skip, crash isolation,
    # cancellation), and the /auth/lichess post-sign-in auto-import kick.
    "llm/tests/test_lichess_analysis.py",
    # Sprint 6.C — hashing defensive branches (HASH_01..HASH_07) and
    # events/storage defensive branches (ESTORE_01..ESTORE_04).  Both
    # were sitting below their per-module floor before these tests
    # because the only existing coverage came transitively from
    # happy-path integration tests.
    "llm/tests/test_auth_hashing.py",
    "llm/tests/test_auth_tokens_import_guards.py",
    "llm/tests/test_event_storage.py",
    # Sprint 6.D — mutation-killer tests for the explain_response_schema
    # boundary validator.  Strict-phrase assertions per Mode-2 branch
    # (content / structure / semantic / schema / empty) so a mutation
    # that swaps "content" → "structure" in a wrap-around message
    # fails loudly.  Adds the file to mutmut's effective test surface.
    "llm/tests/test_explain_schema_mutation_killers.py",
    # Progress dashboard: /player/progress endpoint contract + world-model transparency tests.
    "llm/tests/test_progress_dashboard.py",
    # Training-XP feature (Phase 2): POST /training/solve persistence +
    # idempotency + +10 XP credit per verified solve.  Schema validation
    # + endpoint integration tests run in-memory SQLite against the
    # full SECA Base so every Phase-3 caller has the contract pinned.
    "llm/tests/test_training_solve.py",
    # Send-feedback surface: POST /feedback persistence (message trim +
    # length bounds at the Pydantic boundary, verbatim storage, no
    # message content in logs, route registration on the server app).
    # Schema + endpoint tests run in-memory SQLite against the full
    # SECA Base, same harness as test_training_solve.py.
    "llm/tests/test_feedback_endpoint.py",
    # AI-generated-content reporting (POST /coach/report, CR_01..06): the
    # in-app "Report" affordance Google Play's AI-Generated Content policy
    # requires — stored to the operator moderation queue, content kept out
    # of logs.
    "llm/tests/test_content_report.py",
    # Mistake-replay (Phase 3): first-mistake extraction at
    # /game/finish time + POST /training/verify-replay engine
    # verification.  Detector tests are pool-free; verifier tests
    # stand up a FakePool that returns rigged PovScore objects so
    # the threshold + POV-flip math is pinned.
    "llm/tests/test_mistake_detector.py",
    "llm/tests/test_verify_replay.py",
    # Per-mistake study-plan agent (LLM coaching v1, phase 1 scaffold):
    # 3-puzzle spaced-repetition plan generated as a background task
    # after /game/finish identifies a FirstMistake.  Phase 1 stub
    # (no LLM, no library lookup); phases 2-4 add the verdict,
    # library variants, and Android Home card.  Tests cover the data
    # model, dedup contract, scheduling layout, and endpoint shape.
    "llm/tests/test_study_plan_agent.py",
    # Study-plan Lichess puzzle source: the /api/puzzle/next client wrapper
    # (angle/difficulty allowlist, pgn->solver-FEN derivation, fail-closed
    # legality guard, HTTP-error taxonomy) and the side-matched fetcher that
    # feeds day-3/day-7 (side filter, difficulty fan-out, dedup, bounded call
    # count, feature flag, best-effort []-on-error).  httpx / the client are
    # mocked so no live Lichess call runs in CI.
    "llm/tests/test_lichess_puzzle_fetch.py",
    # Standalone puzzle trainer (Puzzles tab): GET /puzzles/next serves one
    # practice puzzle — Lichess "mix" angle at the player's rating-derived
    # difficulty band, corpus fallback on any Lichess failure, and the
    # PUZZLES_LICHESS_ENABLED kill-switch.  The fetch is monkeypatched (no
    # live Lichess call); allowlist pins keep the router's angle/difficulty
    # constants inside the client's SSRF allowlists.
    "llm/tests/test_puzzles_next.py",
    # Weekly-digest agent (v1) was retired in study-plan phase 4
    # (2026-05-21) — its "top-3 holes + 3 microtasks" framing
    # competed with the per-mistake study-plan agent's Home-screen
    # surface.  The module + test file were deleted; no Android
    # caller depended on it.
    # Bug regression suite: guards the surviving confirmed fixes (reward
    # ZeroDivision, spacing zero-interval, trainer empty-events, bandit
    # empty-actions + singular matrix).  The flat engine_eval / engine_pool
    # bug pins (BUG-5 / BUG-6) were retired in the engine-library cleanup
    # alongside the modules they tested.
    "llm/tests/test_bug_regressions.py",
    # API05 — LLM retry cap, inter-retry backoff delay, and safe fallback contract.
    "llm/tests/test_explain_pipeline_retry.py",
    # CALL_LLM_01–05 — DeepSeek wire-format unit tests against ``call_llm``
    # itself (the retry-test file mocks call_llm at the boundary; this file
    # exercises the function body): missing-API-key fail-fast, content
    # extraction + strip, malformed-response guards, HTTP-error propagation.
    "llm/tests/test_call_llm_deepseek.py",
    # PR 12 — pin documented constant values against their live code
    # source.  Each test imports the constant and asserts the doc text
    # mentions the same value, catching the doc-drift class that
    # surfaced in PRs 6 / 10 / 11.
    "llm/tests/test_doc_constants_pinned.py",
    # QA plan — server-related categories (all 111 pass, zero external deps).
    # INV-01–05: architectural invariants (LLM output, engine format, isolation, call order, game independence).
    "llm/tests/test_architectural_invariants.py",
    # SF-01–04: ESV output schema, centipawn coarsening, malformed-input handling, determinism.
    "llm/tests/test_esv_pipeline_qa.py",
    # RAG-01–04: retrieval determinism, relevance, empty-store safety, document immutability.
    "llm/tests/test_rag_pipeline_qa.py",
    # API-01, API-03, API-06, CI-06: health endpoint, invalid-FEN rejection, seca_doctor, deploy chain.
    "llm/tests/test_api_smoke_qa.py",
    # Adaptive engine wiring: pins ELO range [600, 2400] and the backend→Android
    # strength mapping contract so backend changes break CI immediately.
    "llm/tests/test_adaptive_engine_wiring.py",
    # Server wiring invariants: events/router.py no-print, /explain→SafeExplainer,
    # _record_move_stat safety, log_move call site, unused-model guard.
    "llm/tests/test_server_wiring.py",
    # SECA integration: SafeExplainer schema alignment, live-pipeline quality
    # passthrough, confidence_language_controller wiring, SkillUpdater action
    # derivation, dynamic-mode ELO convergence, and end-to-end ESV chain.
    "llm/tests/test_seca_integration.py",
    # Player level quality: PLQ-01..10 — skill-level mapping correctness, chat and
    # live-move differentiation across beginner/club/intermediate/advanced, SafeExplainer
    # level-aware output, engine signal integrity across all levels.
    "llm/tests/test_player_level_quality.py",
    # Context compaction: COMPACT-01..14 — threshold trigger, length reduction,
    # blunder/strength/topic preservation, recent-turns verbatim, token savings,
    # integration with generate_chat_reply.
    "llm/tests/test_context_compact.py",
    # LLM response quality: QUAL-01..27 — Mode-1/Mode-2 prompt correctness,
    # SafeExplainer level differentiation, ConfidenceLanguageController tone,
    # deterministic fallback adaptation across beginner/intermediate/advanced.
    "llm/tests/test_llm_quality.py",
    # Prompt injection: INJ-01..20 — input sanitizer pattern coverage, field
    # sanitization for player_profile/past_mistakes/FEN/history, output firewall,
    # Mode-2 negative validator, end-to-end injection resistance.
    "llm/tests/test_prompt_injection.py",
    # Stockfish notation correctness: ESV-01..43 — CP threshold boundaries (20/21/60/61/120/121),
    # eval_delta boundaries (49/50), sign convention, mate path, errors type guard,
    # eval_type normalization, value coercion, FEN enrichment, schema round-trip.
    "llm/tests/test_stockfish_notation.py",
    # Post-game AI review of imported Lichess games (API_CONTRACTS §39/§39a):
    # deterministic moment selection + ESV-cutoff banding pins (REVIEW_MOM_*),
    # the LLM writer's Mode-2 gates + compliant-by-construction fallbacks
    # (REVIEW_WRT_*), and the job service + HTTP surface — eligibility,
    # stage idempotency, entitlement cap + per-game readmission, janitor,
    # ownership/error translation (SV_* / RT_*).
    "llm/tests/test_review_moments.py",
    "llm/tests/test_review_writer.py",
    "llm/tests/test_review_endpoints.py",
    # /game/finish idempotent replay (audit 2026-07-14 P2 #1): retried
    # finishes serve the stored response instead of duplicating the
    # GameEvent + rating delta (FIN_IDEM_01..07 — replay payload,
    # pending shape, player scoping, oldest-event-wins, guard placement
    # + IntegrityError race pins).
    "llm/tests/test_game_finish_idempotency.py",
    # In-app notification feed (communication & access spec §5, adapted):
    # feed visibility + badge semantics, lifecycle mutations, the
    # game_analyzed batching producer, the lichess_disconnected alert's
    # dedup + resolution, and the /notifications HTTP surface
    # (NS_* / NR_*).  The import-service integration side (who fires the
    # producers, disconnected_at lifecycle, GET /lichess/status fields)
    # is pinned in test_lichess_import.py DC_01..DC_08.
    "llm/tests/test_notifications.py",
]

COVERAGE_TARGETS = [
    "llm.cache_keys",
    "llm.log_config",
    "llm.metrics",
    "llm.observability",
    "llm.position_input",
    "llm.rag.contracts.validate_output",
    "llm.rag.llm.fake",
    "llm.rag.safety.output_firewall",
    "llm.rag.quality.explanation_score",
    "llm.rag.validators.mode_2_negative",
    "llm.rag.validators.mode_2_semantic",
    "llm.rag.validators.mode_2_structure",
    "llm.rag.validators.sanitize",
    "llm.rag.validators.explain_response_schema",
    "llm.rag.prompts.input_sanitizer",
    "llm.rag.engine_signal.extract_engine_signal",
    "llm.seca.analytics.logger",
    "llm.seca.analytics.events",
    "llm.seca.analytics.mistake_stats",
    "llm.seca.analytics.training_recommendations",
    # llm.seca.coach.chat_pipeline is intentionally excluded from --cov targets:
    # llm.seca.coach.__init__ imports engine.py which loads numpy via a C extension;
    # coverage pre-loading the package triggers "cannot load module more than once
    # per process" when test_chat_pipeline.py later re-imports it.
    # chat_pipeline.py logic is fully exercised by test_chat_pipeline.py (26 tests).
    "llm.seca.events.storage",
    "llm.seca.analysis.historical_pipeline",
    "llm.seca.adaptation.coupling",
    "llm.seca.adaptation.dynamic_mode",
    "llm.seca.adaptation.skill_profile",
    "llm.seca.adaptation.teaching_policy",
    "llm.seca.adaptation.opponent_policy",
    "llm.seca.auth.service",
    "llm.seca.auth.hashing",
    "llm.seca.auth.tokens",
    "llm.seca.legal.router",
    # llm.seca.auth.erasure is intentionally NOT a --cov target: it
    # imports llm.seca.coach.study_plan.models, whose package __init__
    # loads the numpy-backed coach engine — the same "cannot load module
    # more than once per process" pre-load hazard that keeps
    # chat_pipeline excluded above.  test_auth_account_deletion.py
    # (TEST_TARGETS) exercises every branch, including the plan-vs-
    # metadata discovery tripwire.  llm.seca.auth.export and
    # llm.seca.auth.web_deletion are excluded for the same reason (both
    # reach the erasure model graph — web_deletion transitively via its
    # auth.router import); test_auth_data_export.py and
    # test_web_account_deletion.py (TEST_TARGETS) exercise every branch.
    "llm.seca.analytics.router",
    "llm.seca.curriculum.reward",
    "llm.seca.curriculum.spacing",
    # Training-XP surface (Phase 2): TrainingCompletion model + POST
    # /training/solve handler.  Covered by test_training_solve.py
    # listed above in TEST_TARGETS.
    "llm.seca.training.models",
    "llm.seca.training.router",
    # Send-feedback surface: FeedbackMessage model + POST /feedback
    # handler.  Covered by test_feedback_endpoint.py listed above in
    # TEST_TARGETS.
    "llm.seca.feedback.models",
    "llm.seca.feedback.router",
    # Content-report surface: ContentReport model + POST /coach/report.
    # Covered by test_content_report.py in TEST_TARGETS.
    "llm.seca.moderation.models",
    "llm.seca.moderation.router",
    # Mistake-replay (Phase 3): detector picks the FIRST above-threshold
    # player move from losses_cp + PGN; verify runs the engine and
    # answers is_correct.  Covered by test_mistake_detector.py and
    # test_verify_replay.py.
    "llm.seca.mistakes.detector",
    "llm.seca.mistakes.verify",
    # Per-mistake study-plan agent (LLM coaching v1).  Phase 1: data
    # model + stub generator + GET /coach/plan/today endpoint.
    # Phase 2: LLM-generated theme + verdict via single-shot call,
    # gated by Mode-2 negative validator + output firewall.
    # Phase 3: theme-matched library variants for day-3 / day-7
    # puzzles from the curated YAML corpus.
    # Covered by test_study_plan_agent.py.
    "llm.seca.coach.study_plan.models",
    "llm.seca.coach.study_plan.agent",
    "llm.seca.coach.study_plan.router",
    "llm.seca.coach.study_plan.verdict",
    "llm.seca.coach.study_plan.library",
    # Standalone puzzle trainer: GET /puzzles/next (Lichess mix fetch +
    # corpus fallback).  Covered by test_puzzles_next.py.
    "llm.seca.puzzles.router",
    "llm.seca.mistakes.router",
    # Post-game review of imported Lichess games (API_CONTRACTS §39):
    # banded moment selection, Mode-2-gated LLM writer, job service +
    # HTTP surface.  Covered by test_review_moments.py,
    # test_review_writer.py, test_review_endpoints.py.
    "llm.seca.review.models",
    "llm.seca.review.moments",
    "llm.seca.review.writer",
    "llm.seca.review.service",
    "llm.seca.review.router",
    # In-app notification feed: Notification model, feed/badge/producer
    # service, /notifications HTTP surface.  Covered by
    # test_notifications.py + the DC_* class in test_lichess_import.py.
    "llm.seca.notifications.models",
    "llm.seca.notifications.service",
    "llm.seca.notifications.router",
    # llm.seca.coach.live_controller, llm.seca.coach.executor,
    # llm.seca.coach.confidence_language_controller, and
    # llm.seca.coach.explain_pipeline are excluded from --cov targets:
    # llm.seca.coach.__init__ imports engine.py which loads numpy via
    # a C extension; coverage pre-loading the package for instrumentation
    # triggers "cannot load module more than once per process" when the
    # tests later re-import.
    #
    # confidence_language_controller and explain_pipeline were moved
    # into llm.seca.coach.* in the Sprint 4.2 loose-files reorg PR; they
    # were directly covered as top-level modules before the move.  Their
    # logic is fully exercised by test_call_llm_deepseek (5 tests),
    # test_explain_pipeline_retry (3 tests), test_coaching_pipeline_regression
    # (the compute_confidence / compute_urgency / compute_tone /
    # build_language_controller_block suite), test_llm_quality (30 tests),
    # and the firewall integration tests.
    "llm.rag.meta.case_classifier",
    "llm.seca.explainer.safe_explainer",
    # llm.seca.engines.stockfish.pool is intentionally excluded from coverage targets:
    # the majority of its lines require a live Stockfish process and Redis, which are
    # unavailable in the unit-test environment. The pure logic (FenMoveCache, movetime
    # resolution, fallback) is tested via test_engine_response_format.py.
]


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *TEST_TARGETS,
        *[f"--cov={target}" for target in COVERAGE_TARGETS],
        "--cov-report=term-missing:skip-covered",
        "--cov-report=xml:tmp_logs/coverage.xml",
        "--cov-fail-under=80",
        "--junitxml=tmp_logs/pytest-ci.xml",
    ]

    print("=== RUNNING CI TEST SUITE ===")
    pytest_rc = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False).returncode
    if pytest_rc != 0:
        return pytest_rc

    # Per-module coverage floor enforcement on top of pytest's --cov-fail-under
    # global average gate. Validators and the safety firewall must clear 95%
    # — see llm/check_coverage_thresholds.py for the rationale and full list.
    print("=== RUNNING PER-MODULE COVERAGE GATE ===")
    return subprocess.run(
        [sys.executable, "llm/check_coverage_thresholds.py"],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
