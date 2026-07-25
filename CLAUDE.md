# CLAUDE.md

## Project Rules

1. Engine output is the chess source of truth.
2. The LLM explains, but must not override engine truth or bypass ESV.
3. Autonomous RL implementation is prohibited.
4. Tests must remain objective.
5. Never weaken tests or validators to make them pass.
6. Never push before all required validation passes.
7. Prefer minimal, localized, layer-correct changes.
8. The architecture defined in `docs/ARCHITECTURE.md` must not be violated.
9. Commits must describe changes in detail.

## Required Reviews

- Use the governance subagents below at the appropriate stage (see *Subagent Routing*).
- Finish substantial or cross-layer work with the `reviewer` agent (architecture + tests + scope verdict).
- For risky or externally-exposed changes (auth, JNI bridge, validators, freeze guard, network surface), follow `reviewer` with a second pass that explicitly stress-tests hostile input, lifecycle, and memory behavior.
- Preserve API contracts unless docs, tests, and dependent callers are updated together.
- Report blockers explicitly instead of bypassing checks.

## Required Workflow

1. Read this file and the referenced `ARCHITECTURE.md` / `TESTING.md` before editing.
2. Use Explore or equivalent read-only inspection first to find the relevant code paths.
3. Summarize the affected modules and propose a minimal plan before making changes.
4. Modify only the necessary files in the correct layer.
5. Run the relevant validation and fix failures when possible.
6. Finish with the required checks for the layers you touched.

## Subagent Routing

The agents under `.claude/agents/` are stage-gated quality reviewers, not domain specialists. Route by stage of work, not by file location:

- `task-planner` (Opus) — at the start of non-trivial work, decompose into ≤ 6 safe subtasks (one layer per subtask, ≤ 3 files each).
- `executor` (Sonnet) — implement a single subtask precisely; deterministic only, no RL, no test weakening, no contract breaks.
- `scope-guard` (Sonnet) — verify the change touches only the intended layer and only relevant files.
- `diff-guard` (Haiku) — quick diff sanity check: size reasonable, no unrelated files swept in.
- `contract-guard` (Sonnet) — confirm API request/response schemas in `docs/API_CONTRACTS.md` are unchanged or co-updated with code and tests.
- `test-guardian` (Sonnet) — confirm new and existing tests carry real assertions and edge cases (no trivial / weakened checks).
- `ci-guardian` (Haiku) — confirm the relevant test suite passes locally before pushing.
- `command-guard` (Haiku) — wrap any potentially destructive shell command (rm, curl, wget, shutdown, reboot) before execution.
- `reviewer` (Opus) — final pre-commit verdict across architecture, tests, and scope. Required before finishing substantial or cross-layer work.

Domain context (engine pool / JNI / RAG / auth / Android UI) belongs in the prompt to whichever stage-agent is running, not in a separate domain-specialist agent. There is no `architecture-reviewer`, `devils-advocate`, `engine-specialist`, `backend-coach-specialist`, `android-specialist`, or `test-writer` in the current roster — earlier drafts of this document referenced names that were never created.

## Required Checks

- Backend edits should trigger backend-safe checks.
- Android edits should trigger Gradle validation.
- Finishing a task should trigger the configured stop hooks before Claude exits.

## Repo Map

- `llm/`: backend coaching, API, RAG, auth, SECA flows, and backend tests
- `android/`: Android client and Gradle validation surface
- `ios/`: iOS client (SwiftUI; XcodeGen project, built on macOS CI — see `ios/README.md`)
- `engine/`: native engine code and engine-side experiments
- `docs/`: architecture, testing, operations, and release references
- `design/`: React/Babel design canvas mockups (Atrium screens) — visual prototype only, not part of the build
- `.claude/agents/`: project subagents
- `.claude/hooks/`: deterministic governance hooks

## References

- `docs/ARCHITECTURE.md`
- `docs/TESTING.md`

## Developer Setup

> Full instructions: `CLAUDE.md` covers AI agent rules. New developers and human contributors should start here.

### Quick start (Docker)

```bash
cp .env.example .env      # edit values if needed
docker compose up
```

API at `http://localhost:8000`. LLM coaching is provided by [DeepSeek's API](https://platform.deepseek.com); set your key in `.env`:

```bash
# Get a key at https://platform.deepseek.com → API Keys, then:
echo 'COACH_DEEPSEEK_API_KEY=sk-...' >> .env
```

Without it, every `/chat` call falls back to the deterministic template (`chat_pipeline.py:557`) — the API still serves; coaching just degrades to canned responses. `GET /llm/health` reports the live LLM status so you can detect this in monitoring.

### Dev Container (VS Code)

Open the repo and click **"Reopen in Container"**. Installs Python 3.13, Node.js 22, Stockfish, and all Python deps automatically. The DeepSeek API key still needs to be set in `.env` (or as a shell env var) for LLM coaching.

### Android setup

`android/local.properties` is gitignored and machine-specific. Generate it once:

```bash
# macOS / Linux / WSL
./scripts/setup-android.sh

# Windows (PowerShell)
"sdk.dir=$($env:LOCALAPPDATA -replace '\\','/')/Android/Sdk" > android\local.properties
```

Or open `android/` in Android Studio — it generates the file automatically.

The app builds for `arm64-v8a` (physical devices, Apple Silicon emulators) and `x86_64` (Intel/AMD AVDs).

### Python API (bare-metal)

```bash
sudo apt install stockfish   # or: brew install stockfish
pip install -r llm/requirements.txt
cp .env.example .env
python -m uvicorn llm.server:app --host 0.0.0.0 --port 8000
```

Stockfish is auto-detected via `PATH`, then falls back to `/usr/games/stockfish` (Linux) or `engines/stockfish.exe` (Windows). Override with `STOCKFISH_PATH` in `.env`.

### Environment variables

See `.env.example` for the full reference. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SECA_API_KEY` | `dev-key` | Auth key; any value works in `dev` mode |
| `SECA_ENV` | `dev` | `dev` or `prod` |
| `SECRET_KEY` | — | JWT signing secret (≥ 32 chars; required in `prod`) |
| `COACH_DEEPSEEK_API_KEY` | — | **Required** for LLM coaching. Sign up at platform.deepseek.com. |
| `COACH_DEEPSEEK_API_BASE` | `https://api.deepseek.com` | OpenAI-compatible endpoint; override for self-hosted gateways |
| `COACH_DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek-V4 (non-thinking; successor to the retired `deepseek-chat`). Alternative: `deepseek-v4-pro` (thinking, ~higher cost) |
| `STOCKFISH_PATH` | auto-detected | Override Stockfish binary path |
| `REDIS_URL` | *(unset)* | Redis for move cache; omit for in-memory only |
| `STUDY_PLAN_LICHESS_ENABLED` | `1` | Fetch study-plan day-3/day-7 practice puzzles live from Lichess (`/api/puzzle/next`), matched to the day-0 mistake's theme + side + ROLE (defence-themes fetch the `defensiveMove` angle so the player practices saving their own piece), preferring multi-move puzzles. Set `0` to use the local corpus only. Falls back to the corpus on any failure regardless. |
| `STUDY_PLAN_LICHESS_MAX_FETCHES` | `5` | Max `/api/puzzle/next` calls per plan (clamped 1..10). |
| `PUZZLES_LICHESS_ENABLED` | `1` | Puzzles-tab trainer (`GET /puzzles/next`) fetches practice puzzles live from Lichess (`/api/puzzle/next`, "mix" angle, rating-banded difficulty). Set `0` to serve the local corpus only. Falls back to the corpus on any failure regardless. |
| `TRUSTED_PROXIES` | prod: empty (XFF not trusted, warning logged); dev: loopback | Comma-separated proxy IPs / CIDRs. **Required in prod** for per-client rate limiting; otherwise every request behind the reverse proxy keys on the same bucket. See README and `docs/DEPLOYMENT.md` > Trusted Proxies. |

**HTTP-level contracts** (no env vars; pinned constants):

- `X-API-Version: 1` — every coaching request from the Android client carries
  this header; the server enforces a matching value (Phase 1 lenient on
  missing, strict on mismatch). Bumping requires a coordinated server +
  Android release. See `docs/API_CONTRACTS.md` > API schema versioning.

### Running tests

```bash
python llm/run_ci_suite.py                    # Python CI suite
python llm/run_quality_gate.py black          # formatting
python llm/run_quality_gate.py pylint         # linting
python llm/run_quality_gate.py mypy           # types
cd android && ./gradlew test                  # Android host JVM tests
cd android && ./gradlew connectedAndroidTest  # Android instrumented tests
```

### Common issues

| Symptom | Fix |
|---------|-----|
| `UnsatisfiedLinkError: libchessengine.so` | Expected on host JVM — `NativeEngineProvider` returns `null` gracefully. Real test runs on device/emulator via `connectedAndroidTest`. |
| `FileNotFoundError: stockfish` | Install Stockfish or set `STOCKFISH_PATH` in `.env`. |
| `host.docker.internal` unreachable on Linux | `docker-compose.yml` adds `host-gateway` automatically. For bare `docker run`, add `--add-host=host.docker.internal:host-gateway`. |
| `sdk.dir` Gradle error | Run `./scripts/setup-android.sh` or open `android/` in Android Studio. |
