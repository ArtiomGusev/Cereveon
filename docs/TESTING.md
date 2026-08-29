TESTING.md
Purpose

This document defines mandatory and optional tests for the ChessCoach-AI Mode-2 system.

Tests exist to guarantee:

correctness

safety

non-hallucination

long-term stability

No test exists to measure chess strength or stylistic quality.

Definitions

Golden test: deterministic test with fixed expected output

Contract test: behavioral constraint on LLM output

Smoke test: basic execution check

Regression test: repeated execution to detect drift

CI: Continuous Integration (GitHub Actions)

Test Categories
Category A — Golden Tests (MANDATORY)

Purpose

Lock deterministic logic

Prevent prompt drift

Prevent retrieval drift

Scope

Engine → ESV mapping

RAG retrieval

Mode-2 prompt injection — the snapshot pins the PRODUCTION composition
(the live `system_v2_mode_2.SYSTEM_PROMPT` + the `render_mode_2`
renderer that `llm/rag/deploy/embedded.py` uses), so rewriting the
production prompt breaks a per-push test and forces a deliberate
snapshot regeneration (`llm/scripts/regenerate_prompt_snapshots.py`).
Category D renders through the same canonical `render_case`, so the
weekly-regressed prompt is byte-identical to the pinned one.  (The
former `mode_2/system_v1.txt` / `render_v1` pair had zero production
consumers and was retired 2026-07-06.)

Commands

python -m pytest -q llm/rag/tests/golden/test_retriever.py
python -m pytest -q llm/rag/tests/golden/test_prompt_snapshot.py


Rules

Must always pass

Must produce no output on success

Any failure blocks merge

CI

Yes

Category B — LLM Contract Tests (MANDATORY)

Purpose

Enforce LLM behavior rules

Prevent hallucinations

Prevent engine leakage

Scope

Forbidden phrases

Forced-mate handling

Missing-data handling

LLM Used

Fake LLM only

Command

python -m pytest -q llm/rag/tests/contracts/test_fake_llm.py


Rules

Must always pass

Must run in CI

Validators must never be weakened

CI

Yes

Category C — Real LLM Smoke Test (OPTIONAL, LOCAL ONLY)

Purpose

Verify real LLM connectivity

Verify validators accept real output

Scope

DeepSeek API call against the live coaching endpoint

Output passes contract validators

Command

RUN_DEEPSEEK_TESTS=1 COACH_DEEPSEEK_API_KEY=sk-... \
    python -m pytest -q llm/rag/tests/llm/test_deepseek_smoke.py


Rules

Must not run in CI

Failure indicates environment or model issue

No golden expectations

CI

No

Category D — LLM Regression Tests (OPTIONAL, LOCAL ONLY)

Purpose

Detect model drift

Detect intermittent violations

Scope

Repeated runs of real LLM

Contract compliance over time

Command

python -m pytest -q llm/rag/tests/llm/test_llm_regression.py


Rules

Must not run in CI

Any failure indicates instability

Validators must not be relaxed to fix failures

CI

No

Category E — Quality Heuristic Tests (OPTIONAL)

Purpose

Detect explanation degradation

Assist human review

Scope

Length heuristics

Sentence structure

Non-triviality

Command

python -m pytest -q llm/rag/tests/quality/test_explanation_quality.py


Rules

Must never block CI

Failures are advisory only

No exact text matching

CI

No

Category F — Validator Mutation Tests (OPTIONAL, LOCAL ONLY)

Purpose

Verify that the validators are not just *executed* by tests, but
*tested* by tests.  Line coverage answers "did the test execute the
validator?" — mutation testing answers the harder question: "would
the test fail if the validator's logic were wrong?"  A test that
imports a validator and asserts no exception is raised counts as
100% line coverage but catches no logic mutations; mutmut surfaces
exactly that gap by mutating each operator / constant / branch in
turn and confirming a test fails for each one.

Scope

llm/rag/validators/mode_2_negative.py
llm/rag/validators/mode_2_structure.py
llm/rag/validators/mode_2_semantic.py
llm/rag/validators/sanitize.py
llm/rag/contracts/validate_output.py
llm/rag/safety/output_firewall.py

Tools

mutmut, run via scripts/run_mutation_tests.sh.

Command

bash scripts/run_mutation_tests.sh                  # all validators
bash scripts/run_mutation_tests.sh mode_2_negative  # one module

Rules

Must not run in CI (minutes-to-tens-of-minutes per validator)

Surviving mutants are TODOs, not test failures: each one is a missing
test case that did not catch a logic mutation.

Run before any substantive change to a validator, before any release
that touches llm/rag/validators/, and at least quarterly during
architectural review.

Validators must never be relaxed to kill a surviving mutant — fix the
test instead, or accept the gap with a documented rationale.

CI

No

Category G — Real-LLM Stress Test (OPTIONAL, LOCAL ONLY)

Purpose

Probe the two production pipelines (Mode-1 live-move, Mode-2 chat) with a
matrix of engine-ground-truthed positions and check every FINAL user-visible
reply with deterministic claim-checkers, on three axes:

Hallucination — phantom pieces (a piece type neither side has), phantom
checks (check claims on a check-free board).  Zero tolerance.

Accuracy — advantage-direction inversion ("you are winning" when the engine
says the opponent is), material claims contradicting the board, Mode-1
praise/condemnation contradicting the engine's move grade.  Zero tolerance.
These are claims NO in-pipeline validator enforces (only the prompt framing
does) — which is exactly why they are stress axes.

Style — Mode-1 1–2-sentence contract, beginner-level jargon lexicon, terse
coach voice length, chat length budget.  Rate-based (>= 80%) over REAL LLM
replies only, because style is a prompt contract on a stochastic model —
deterministic fallback templates are pinned by their own per-push tests.

Also reports fallback rate per mode (fails under 70% — over-rejection
signal on an adversarial matrix) and latency percentiles (telemetry only).

Scope

llm/rag/tests/llm/test_llm_stress.py (probe matrix + deterministic
claim-checkers + checker self-checks)

Command

RUN_DEEPSEEK_TESTS=1 COACH_DEEPSEEK_API_KEY=sk-... \
    python -m pytest -q llm/rag/tests/llm/test_llm_stress.py

Set LLM_STRESS_REPORT_PATH=/path/report.jsonl to persist per-probe
telemetry (reply, fallback flag, violations, latency).

Rules

Must not run in per-push CI (real model; runs weekly via
llm-regression-cron.yml alongside Categories C and D)

Hallucination / accuracy failures are contract violations — treat like a
Category B/D failure: fix prompt, grounding, or model settings; never
weaken the checkers or thresholds

Style failures below the rate threshold indicate prompt drift

The claim-checkers are pinned by in-module self-check tests so a broken
regex cannot silently turn an axis into a no-op

CI

No (weekly cron only)

Required Test Runs
Before pushing code
python llm/run_quality_gate.py
python llm/run_ci_suite.py
python -m pytest -q llm/rag/tests/golden/test_retriever.py
python -m pytest -q llm/rag/tests/golden/test_prompt_snapshot.py
python -m pytest -q llm/rag/tests/contracts/test_fake_llm.py
python -m pytest -q llm/rag/tests/contracts/test_violations_corpus.py
python -m pytest -q llm/rag/tests/test_output_firewall.py
python -m pytest -q llm/tests/test_api_contract_validation.py
python -m pytest -q llm/tests/test_coaching_pipeline_regression.py

Before release (local; all require COACH_DEEPSEEK_API_KEY in env)
RUN_DEEPSEEK_TESTS=1 python -m pytest -q llm/rag/tests/llm/test_deepseek_smoke.py
RUN_DEEPSEEK_TESTS=1 python -m pytest -q llm/rag/tests/llm/test_llm_regression.py
RUN_DEEPSEEK_TESTS=1 python -m pytest -q llm/rag/tests/llm/test_llm_stress.py

CI Policy

CI runs the following on every push (authoritative source:
`.github/workflows/fly-deploy.yml`):

python llm/run_quality_gate.py black            # Black formatting

python llm/run_quality_gate.py pylint           # Pylint

python llm/run_quality_gate.py mypy             # Mypy (strict on trust-boundary modules)

python -m pytest -q llm/rag/tests/golden/test_retriever.py llm/rag/tests/golden/test_prompt_snapshot.py

python -m pytest -q llm/rag/tests/contracts/test_fake_llm.py

python -m pytest -q llm/rag/tests/contracts/test_violations_corpus.py

python -m pytest -q llm/tests/test_api_contract_validation.py

python -m pytest -q llm/tests/test_coaching_pipeline_regression.py

python -m pytest -q llm/tests/test_explain_schema_validation.py

python -m pytest -q llm/tests/test_engine_response_format.py llm/tests/test_engine_pool_evaluate_position.py llm/tests/test_engine_pool_exhaustion.py llm/tests/test_engine_pool_crash_recovery.py llm/tests/test_fen_move_cache_key.py llm/tests/test_stockfish_adapter_isolation.py

python -m pytest -q llm/tests/test_api_security.py

python llm/run_regression_suite.py              # 6 grouped invocations: engine, coaching, contract+security, analysis, layer-boundaries, golden — fails fast on first broken group

python llm/run_ci_suite.py                      # authoritative coverage gate; also runs test_output_firewall.py


The golden tests, LLM contract tests (`test_fake_llm.py` AND
`test_violations_corpus.py`), API contract validation, coaching pipeline
regression, explain schema validation, engine pool regressions, and API
security tests each run as explicit named steps in the python-tests CI job
so failures surface immediately in the GitHub Actions UI.
`run_regression_suite.py` follows with a grouped re-run that fails fast at
the first broken group; `run_ci_suite.py` runs last as the authoritative
coverage gate and also covers the output-firewall positive-case tests
(`test_output_firewall.py`) referenced by the Validator Coverage Matrix
below.

CI quality gates also enforce:

Black formatting checks

Pylint checks on the stable Python surface

Mypy checks on the typed utility surface

Coverage fail-under 80% for the CI-covered Python modules

pip-audit and Trivy security scans


CI must never run:

real-LLM tests — `llm/rag/tests/llm/test_deepseek_smoke.py`,
`test_llm_regression.py`, and `test_llm_stress.py` are local-only and run
weekly via `.github/workflows/llm-regression-cron.yml`

quality heuristics — Category E is advisory only

mutation tests — Category F is local-only (minutes per validator)

The term *regression test* is overloaded in this project. The
**coaching-pipeline regression** (`test_coaching_pipeline_regression.py`)
IS a per-push CI test — it pins deterministic behavior of the chat
pipeline and freeze guards. The **LLM-behavior regression**
(`test_llm_regression.py`) is the one excluded from per-push CI — it
needs a real model and is run on demand / weekly cron only.

Validator Coverage Matrix

This matrix maps every advertised forbidden behavior to the validator that
enforces it, the pattern or check, the positive test that confirms clean output
passes, the negative test (corpus entry ID) that confirms the violation is
rejected, and the real-LLM regression test that exercises the rule end-to-end.

A reviewer can audit completeness by reading down the leftmost column: every
safety claim in the README and ARCHITECTURE.md must appear here, with all five
test cells populated. Empty cells are open coverage gaps and must be tracked.

**Single source of truth for the rule data.** As of 2026-05-14 (PR
"validator consolidation"), every shared phrase / pattern set used by
the Mode-2 validators (rows 1–9) lives in
[`llm/rag/validators/_rules.py`](../llm/rag/validators/_rules.py).  The
public validator modules — `validate_output`, `mode_2_negative`,
`mode_2_structure`, `mode_2_semantic` — re-export these under their
historical public names so the 29-callsite import surface keeps working
unchanged.  The "Implemented by" column below names the validator entry
point; the rule data itself is one named constant per row in `_rules.py`
(cited inline).  The output-firewall rows (10–14) keep their categories
in `llm/rag/safety/output_firewall.py` — concerns there are non-chess
(PII / identity / prompt-leak / bypass / harmful) and already cleanly
named.

Negative-test cells reference the `id` field of an entry in
`llm/rag/tests/contracts/fixtures/violations.jsonl` (driven by
`test_violations_corpus.py`). Positive-test cells reference pytest node IDs in
the CI suite. The single regression-test ID applies wherever a real-LLM check
is meaningful — `output_firewall` rules are deterministic and need no real-LLM
regression because the regex set IS the contract.

| # | Forbidden behavior | Implemented by | Pattern / check | Positive test (clean passes) | Negative test (violation rejected) | Real-LLM regression |
|---|---|---|---|---|---|---|
| 1 | Engine mention | `validate_output` (`llm/rag/contracts/validate_output.py`) + `validate_mode_2_negative` (`llm/rag/validators/mode_2_negative.py`) | FORBIDDEN_PHRASES (`stockfish`, `best move`, `engine`, `depth`, `calculate`, `variation`); FORBIDDEN_PATTERNS includes `\bcalculate\b`, `\bcalculation\b`, `\bvariation\b`, `\bstockfish\b`, `\bbest move\b`, `\bdepth\s+\d+\b` (last three added 2026-06-22 so the live /chat·/live-move·/stream boundary — which runs `validate_mode_2_negative`, NOT `validate_output` — enforces them too; `depth` uses the engine-sense form `\bdepth\s+\d+\b` ("depth 18") to avoid vetoing "in depth" coaching prose, while `validate_output` keeps the broader bare-substring "depth"; `engine` stays semantic-only via `SPECULATIVE_SEMANTIC`; `\bline\b` retired 2026-06-07 — over-broad: "open lines") | `test_fake_llm.py::test_compliant_output_passes` | `violations.jsonl::ENG-01..03`, `USR-01` | `test_llm_regression.py::test_llm_regression_contract` |
| 2 | Move suggestion (algebraic notation) | `validate_mode_2_negative` | `\b[KQRBN]?[a-h][1-8]\b` (piece letter optional → pawn squares too, 2026-06-05), `\b0-0(?:-0)?\b` and `\bO-O(?:-O)?\b` | `test_fake_llm.py::test_compliant_output_passes` | `violations.jsonl::MOV-01..02`, `USR-01` | `test_llm_regression.py::test_llm_regression_contract` |
| 3 | Move suggestion (advisory prose) | `validate_mode_2_structure` (`llm/rag/validators/mode_2_structure.py`) | FORBIDDEN_SECTIONS: `recommended move`, `example move`, `white can`, `black can` (`plan`/`if it`/`consider` retired 2026-06-07 as over-broad coaching phrases) | `test_fake_llm.py::test_compliant_output_passes` | `violations.jsonl::MOV-03..05`, `USR-02` | `test_llm_regression.py::test_llm_regression_contract` |
| 4 | Speculative language | `validate_mode_2_negative` (lexical) + `validate_mode_2_semantic` (semantic-surface mirror, defense in depth) | `\bI think\b`, `\bplans to\b`, `\bthe engine wants\b`, `\bcarelessly\b`, `\black of planning\b`, `\bwith perfect play\b`, `\bactually winning\b` (`\bshould\b` retired PR #170; `\blikely\b`/`\bprobably\b`/`\bconsider\b` retired 2026-06-07 as hedged coaching, not engine-fact speculation); `mode_2_semantic` re-flags only `engine` (whole-word) now | `test_fake_llm.py::test_compliant_output_passes` | `violations.jsonl::SPE-01..02`, `USR-02`, `SEM-04` (semantic surface) | `test_llm_regression.py::test_llm_regression_contract` |
| 5 | Mate misframing (claim outside engine) | `validate_mode_2_negative` (lexical) + `validate_mode_2_semantic` (decisive-mate require, defense in depth) | `\bcheckmate\b`, `\bmate in \d+\b`, `\bforce(?:d)? mate\b`, `\bgame ends here\b`; `mode_2_semantic` additionally requires `inevitable` / `forced` when `engine_signal.evaluation.type == "mate"` so an LLM that elides the mate frame entirely (no lexical hit) still fails at the semantic check | `test_fake_llm.py::test_compliant_output_passes` | `violations.jsonl::MAT-01`, `USR-02`, `SEM-03` (semantic surface) | `test_llm_regression.py::test_llm_regression_contract` |
| 6 | Mate misframing (missing inevitability) | `validate_output` REQUIRED_ON_MATE | `case_type=forced_mate` ⇒ output must include `inevitable` / `cannot be avoided` / `unavoidable` | `test_fake_llm.py::test_compliant_output_passes` (compliant text contains "inevitable") | `violations.jsonl::MAT-02`, `test_fake_llm.py::test_mate_softening_fails` | `test_llm_regression.py::test_llm_regression_contract` |
| 7 | Missing-data refusal | `validate_output` REQUIRED_ON_MISSING | `case_type=missing_data` ⇒ output must include `missing` / `not enough information` | `test_fake_llm.py::test_compliant_output_passes` | `violations.jsonl::MIS-01`, `test_fake_llm.py::test_missing_data_violation_fails` | `test_llm_regression.py::test_llm_regression_contract` |
| 8 | Invented evaluation | `validate_mode_2_semantic` (`llm/rag/validators/mode_2_semantic.py`) | `engine_signal.evaluation.band == "equal"` ⇒ text must NOT contain `slight advantage` / `winning` (WHOLE-WORD) ⇒ `Mode2Violation` (`initiative` / `pressure` retired 2026-06-06; `better` retired 2026-06-07 — too common a comparative; substring→word-boundary 2026-06-07) | `test_fake_llm.py::test_compliant_output_passes` (smoke) | `violations.jsonl::SEM-01` | `test_llm_regression.py::test_llm_regression_contract` |
| 9 | Invented tactics | `validate_mode_2_semantic` | `engine_signal.tactical_flags == []` ⇒ text must NOT contain `fork` / `pin` / `sacrifice` (WHOLE-WORD since 2026-06-07 — "pin" no longer matches "develoPINg"/"stepPINg") ⇒ `Mode2Violation` (`attack` / `threat` retired 2026-06-06 — general strategic words, not concrete tactical motifs) | `test_fake_llm.py::test_compliant_output_passes` (smoke) | `violations.jsonl::SEM-02` | `test_llm_regression.py::test_llm_regression_contract` |
| 10 | Prompt leakage | `output_firewall.check_output` (`llm/rag/safety/output_firewall.py`) | `_CAT_P` regex set ("system prompt says", "I am instructed to", etc.) ⇒ `OutputFirewallError(category="PROMPT_LEAK")` | `test_output_firewall.py::TestCleanOutput::*` | `violations.jsonl::FW-PROMPT-01` | (deterministic — no real-LLM regression needed) |
| 11 | Role bypass | `output_firewall.check_output` | `_CAT_B` regex set (DAN mode, "restrictions disabled", etc.) ⇒ `OutputFirewallError(category="BYPASS")` | `test_output_firewall.py::TestCleanOutput::*` | `violations.jsonl::FW-BYPASS-01` | (deterministic) |
| 12 | Identity confusion | `output_firewall.check_output` | `_CAT_I` regex set ("I am ChatGPT", "I am a real person", etc.) ⇒ `OutputFirewallError(category="IDENTITY")` | `test_output_firewall.py::TestCleanOutput::*` | `violations.jsonl::FW-IDENTITY-01` | (deterministic) |
| 13 | PII / credential leakage | `output_firewall.check_output` | `_CAT_D` regex set (email, API-key shape, password assignment) ⇒ `OutputFirewallError(category="PII_CREDENTIAL")` | `test_output_firewall.py::TestCleanOutput::*` | `violations.jsonl::FW-PII-01..02` | (deterministic) |
| 14 | Harmful instructions | `output_firewall.check_output` | `_CAT_H` regex set (how-to-make-bomb, step-by-step hacking, self-harm, etc.) ⇒ `OutputFirewallError(category="HARMFUL")` | `test_output_firewall.py::TestCleanOutput::*` | `violations.jsonl::FW-HARMFUL-01` | (deterministic) |

The corpus contract test (`test_violations_corpus.py`) carries a final assertion
`test_corpus_covers_every_advertised_safety_rule` that fails when a row above
exists without a corresponding `violations.jsonl` entry — keeping the matrix
honest as the validators evolve.

Enforcement Rules

Golden failures indicate logic or prompt regressions

Contract failures indicate safety violations

Regression failures indicate model instability

Quality failures indicate possible UX degradation only

No test category may be removed without replacement.

Invariants

If all CI tests pass, the system is guaranteed to be:

deterministic

non-hallucinatory

rule-compliant

regression-protected

Non-Goals

This test suite does NOT:

evaluate chess strength

optimize wording

rank models

measure creativity


LLM Regression Test Frequency (MANDATORY POLICY)
Definition

LLM regression tests are designed to detect behavior drift over time in real language models.

They are not continuous tests and are not CI tests.

Required Frequency

LLM regression tests MUST be run in the following situations:

Before any release

After any system prompt change

After any RAG document content change

After updating or replacing the LLM model
(e.g., bumping `COACH_DEEPSEEK_MODEL` from `deepseek-v4-flash` to `deepseek-v4-pro`,
or switching to a different OpenAI-compatible provider)

Command:

python -m pytest -q llm/rag/tests/llm/test_llm_regression.py

Prohibited Usage

LLM regression tests MUST NOT be:

Run on every commit

Run in CI

Used to evaluate explanation quality

Used to compare models subjectively

They exist only to detect contract violations.

Failure Interpretation

If an LLM regression test fails:

The model behavior is considered unstable

Validators must NOT be weakened

The failure must be addressed by:

lowering temperature

tightening the system prompt

adjusting RAG phrasing

changing model variant

Ignoring a regression failure is not permitted.

Relationship to Other Tests
Test Type	Frequency
Golden tests	Every commit
Contract tests	Every commit
API contract validation	Every commit
Coaching pipeline regression	Every commit
Regression tests	On change events
Quality tests	On demand
Enforcement Rule

A release is invalid unless LLM regression tests pass immediately prior to release.

Invariant

If LLM regression tests pass at release time, then:

Model behavior is contract-stable

No intermittent hallucinations are present

Production deployment is permitted

Android Instrumented Tests

Host-JVM unit tests cover most of the Android client (`./gradlew :app:testDebugUnitTest`). A separate **instrumented** suite under `android/app/src/androidTest/` runs on a real Android runtime — the Atrium layout-inflation smoke suite (AAPT2 link errors / theme-attribute mismatches / drawable-not-found bugs host-JVM tests can't see), the JNI engine smoke test, `ChessBoardView` position-state re-seeding (`loadPosition` — the puzzle-board freeze regression: game-over latch, castling flags, en-passant target), and the study-plan drill's multi-move walk on a live fragment (`TodaysDrillWalkInstrumentedTest`, FragmentScenario + fake `GameApiClient`).

To run the instrumented suite end-to-end:

`bash scripts/run_connected_android_tests.sh`

The script verifies the SDK + cmdline-tools install, creates a headless AVD if none exist (`atrium_test`, x86_64 Pixel 5 by default), boots the emulator, runs `./gradlew :app:connectedAndroidTest`, and tears the emulator down on success or failure. Idempotent — re-running with an AVD already created reuses it.

One-time prerequisite: install **Android SDK Command-line Tools (latest)** via Android Studio → Settings → Android SDK → SDK Tools tab. The script's preflight check fails loudly with remediation steps when this is missing.

Override knobs (env vars): `AVD_NAME`, `SYSTEM_IMAGE`, `DEVICE_PROFILE`, `BOOT_TIMEOUT_SECONDS`. Use `--keep-running` to leave the emulator up after tests for iterative debugging.

CI cadence

The instrumented suite runs nightly (03:00 UTC) on GitHub-hosted Ubuntu runners with KVM acceleration via the `.github/workflows/android-instrumented.yml` workflow.  Boots a Pixel 5 AVD on API 36 / x86_64 / google_apis, runs `./gradlew :app:connectedAndroidTest`, and uploads HTML + XML reports as artifacts on every run.  AVD snapshots are cached so the per-run boot cost stays under a minute after the first run on a given cache key.

The workflow also accepts `workflow_dispatch` — a developer iterating on JNI or theme code can trigger an ad-hoc CI run from the Actions tab without waiting for the schedule.

The instrumented suite is **not** part of the per-push pipeline: connectedAndroidTest takes 15-30 minutes end-to-end, and adding it to every push would dominate PR latency for a relatively small marginal coverage win (the host-JVM suite catches most regressions).

End of TESTING.md
