# Deployment Runbook

ChessCoach-AI — production deployment checklist and operational reference.

---

## 0. Production Topology

Production runs a single tier, built and signed by the CI pipeline:

| Tier | Image | Source | Port | Role |
|---|---|---|---|---|
| **Hetzner backend** | `cereveon-llm-api` | [`llm/Dockerfile.api`](../llm/Dockerfile.api) (Python + Stockfish + full SECA pipeline) | 8000 | Everything: public ingress at `cereveon.com` behind Caddy, engine pool, RAG, validators, auth, Postgres + Redis stack from [`docker-compose.prod.yml`](../docker-compose.prod.yml). LLM coaching via DeepSeek API. |

**Auto-deployed by [`.github/workflows/fly-deploy.yml`](../.github/workflows/fly-deploy.yml)** on push to `main`:

- The `deploy` job SSHes to Hetzner and runs the zero-downtime rolling swap (scale=2, health-gate, drain-or-rollback).

The workflow filename is historical (it pre-dates both the rename to a unified CI/CD pipeline and the removal of the Fly edge). The workflow's `name:` field is `CI/CD`, which is authoritative.

> **Removed 2026-07-15 — the Fly.io edge.** A second tier once existed on
> Fly.io (`chesscoach` app, root `Dockerfile` → `llm/server.js`, a Node +
> Express **Ollama prototype**). It was documented here as a "load-bearing
> public entry point," but it never was: `cereveon.com` has always
> resolved to Hetzner's Caddy directly, and the Fly app's only routes were
> an unauthenticated, validator-bypassing `/coach`·`/explain` that 502'd on
> a non-existent Ollama upstream. The edge image, `fly-deploy` job, Node
> dependency audit, and `FLY_API_TOKEN` were removed. **To finish tearing
> it down**, destroy the still-running Fly app once: `flyctl apps destroy
> chesscoach`.

### Manual updates

Deploy manually from a workstation:

- Hetzner: `gh workflow run "Production Deploy" -f api_digest=sha256:...` (the [`production-deploy.yml`](../.github/workflows/production-deploy.yml) workflow shares the same `hetzner-production` concurrency group as the auto deploy, so the two cannot race).

---

## 1. Required Environment Variables

Set these before starting the server. Missing required variables cause an
explicit `RuntimeError` at startup — the server will not start.

### Server (backend)

| Variable | Required in prod | Default | Description |
|----------|-----------------|---------|-------------|
| `SECA_ENV` | yes | `dev` | Set to `prod`. Enables JWT enforcement and disables debug output. |
| `SECA_API_KEY` | yes | *(none)* | API key for `X-Api-Key` protected routes. Any non-empty string. Server aborts startup if unset when `SECA_ENV=prod`. |
| `SECRET_KEY` | yes | *(random, ephemeral)* | JWT signing secret. Must be ≥ 32 characters. In dev an ephemeral key is generated; all tokens are invalidated on restart. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `CORS_ALLOWED_ORIGINS` | yes | *(empty — blocks all cross-origin)* | Comma-separated list of allowed CORS origins (e.g. `https://app.example.com`). Empty value blocks all cross-origin requests and logs a warning. |
| `COACH_DEEPSEEK_API_KEY` | yes | *(none)* | DeepSeek API key (sign up at platform.deepseek.com). Without it, every `/chat` call falls back to the deterministic template — coaching degrades, doesn't error. |
| `COACH_DEEPSEEK_API_BASE` | no | `https://api.deepseek.com` | OpenAI-compatible endpoint. Override only for self-hosted gateways. |
| `COACH_DEEPSEEK_MODEL` | no | `deepseek-v4-flash` | DeepSeek-V4 non-thinking. ~$0.14/M in + $0.28/M out. |
| `STOCKFISH_PATH` | no | auto-detected | Override path to Stockfish binary. Auto-detection checks `PATH`, then `/usr/games/stockfish` (Linux) or `engines/stockfish.exe` (Windows). |
| `DATABASE_URL` | no | `sqlite:///data/seca.db` | SQLAlchemy DB URL. Use Postgres in production for multi-worker deployments. |
| `REDIS_URL` | no | *(in-memory only)* | Redis URL for persistent move cache. Omit to use local in-memory cache. |

### Android client (build-time)

| Build config field | Required in release | Default | Description |
|--------------------|--------------------|---------| ------------|
| `COACH_API_BASE` | yes | `http://10.0.2.2:8000` | Base URL of the backend API. Release builds must use `https://`. Set via the `COACH_API_BASE` environment variable at build time (CI secret injection) or in `build.gradle.kts`. |
| `COACH_API_KEY` | yes | *(dev fallback)* | Value sent as `X-Api-Key`. Must match `SECA_API_KEY` on the server. Set via `COACH_API_KEY` env var in CI. |

---

## 2. Startup Assertions

The server performs these checks on startup and fails hard if they are not met:

| Check | Failure mode | Resolution |
|-------|-------------|------------|
| `SECA_API_KEY` set when `SECA_ENV=prod` | `RuntimeError` at import time | Set a non-empty `SECA_API_KEY` |
| Stockfish binary reachable | Engine pool disabled; move endpoints return `{"error": "engine pool unavailable"}` | Install Stockfish or set `STOCKFISH_PATH` |
| `COACH_DEEPSEEK_API_KEY` set and DeepSeek reachable | Coaching/chat/explain fall back to deterministic template (logged at WARNING by chat_pipeline.py:557) | Set the API key in `.env.prod`, restart api. `GET /llm/health` reports the live status. |
| `CORS_ALLOWED_ORIGINS` non-empty | Warning logged; all cross-origin requests blocked | Set at least one origin |
| DB migration / table creation | Exception at startup | Check `DATABASE_URL` and that the DB is reachable |

Silent failures are not acceptable. Confirm startup log shows no warnings from
any of the checks above before directing traffic to a new instance.

---

## 3. Health Check

```
GET /health
```

**Auth:** none
**Response:** `{"status": "ok"}` with HTTP 200

Use this route for load-balancer health checks and readiness probes.

> **Note:** A 200 response from `/health` confirms the process is alive and
> FastAPI is serving. It does not verify that the engine pool or LLM provider
> are functional. For a deeper liveness check, call `GET /debug/engine`
> (requires `X-Api-Key`) and confirm `pool_size > 0`, and `GET /llm/health`
> (open) and confirm `ok: true`.

---

## 4. Startup Sequence

```bash
# 1. Copy and populate environment
cp .env.example .env
# edit .env: set SECA_ENV=prod, SECA_API_KEY, SECRET_KEY, CORS_ALLOWED_ORIGINS, ...

# 2. Confirm DeepSeek API key is set in .env (COACH_DEEPSEEK_API_KEY=sk-...)

# 3. Start the server
python -m uvicorn llm.server:app --host 0.0.0.0 --port 8000 --workers 4
```

Or via Docker Compose:

```bash
docker compose up --build
```

---

## 5. Smoke Tests After Deploy

Run the automated smoke test script (requires `curl` and `python3`):

```bash
# From the repo root on any machine with network access to the server:
./scripts/smoke_test.sh https://api.yourdomain.com "$SECA_API_KEY"

# Or locally against a running dev instance:
./scripts/smoke_test.sh http://localhost:8000 dev-key
```

The script performs three checks and exits non-zero on any failure:

1. `GET /health` → `{"status": "ok"}`
2. `GET /debug/engine` with `X-Api-Key: <key>` → `pool_size > 0`
3. `POST /engine/eval` with the starting FEN → `best_move` is non-null

After confirming the script passes, check the server logs for startup warnings
(CORS, engine pool, DB) and probe `GET /llm/health` to confirm DeepSeek
connectivity.

---

## 6. CI/CD Secrets and Variables

These must be configured in the GitHub repository before the `deploy` job will
run. Go to **Settings → Secrets and variables → Actions**.

### Secrets (encrypted, never logged)

| Secret name | Where used | How to obtain |
|-------------|------------|---------------|
| `HETZNER_HOST` | SSH deploy step — target address | IP or hostname of your Hetzner VPS |
| `HETZNER_SSH_KEY` | SSH deploy step — private key | Generate with `ssh-keygen -t ed25519`; add the public key to `/home/deploy/.ssh/authorized_keys` on the server (user `deploy`) |
| `COACH_API_KEY` | Android release APK build — baked in as `X-Api-Key` | Any non-empty string; **must match `SECA_API_KEY` in `.env.prod` on the server** |
| `KEYSTORE_BASE64` | Android release APK signing | Base64-encode your `.jks` file: `base64 -w 0 release.jks` (Linux/macOS) or `[Convert]::ToBase64String([IO.File]::ReadAllBytes("release.jks"))` (PowerShell) |
| `KEY_ALIAS` | Android release APK signing | The alias chosen when running `keytool -genkey` |
| `KEY_PASSWORD` | Android release APK signing | The key password chosen when running `keytool -genkey` |
| `STORE_PASSWORD` | Android release APK signing | The store password chosen when running `keytool -genkey` |
| `FEEDBACK_SMTP_USERNAME` | Weekly feedback digest — SMTP login + `From:` address | The mailbox that sends the digest (for Gmail: the full address) |
| `FEEDBACK_SMTP_PASSWORD` | Weekly feedback digest — SMTP auth | For Gmail: an **app password** (Google account → Security → 2-Step Verification → App passwords); the normal account password will not work |
| `FEEDBACK_DIGEST_TO` | Weekly feedback digest — recipient | The operator address the digest is mailed to |
| `FEEDBACK_SMTP_HOST` | Weekly feedback digest — optional | SMTP server; defaults to `smtp.gmail.com` when unset |
| `FEEDBACK_SMTP_PORT` | Weekly feedback digest — optional | SMTP port; defaults to `465` (implicit TLS) when unset |

> `GITHUB_TOKEN` is auto-provisioned by Actions. It is used for GHCR push,
> image attestation, and Trivy scanning. No configuration required.

### Variables (plaintext, visible in logs)

| Variable name | Where used | Example |
|---------------|------------|---------|
| `COACH_API_BASE` | Android release APK build — backend URL | `https://api.yourdomain.com` |

### Server-side environment (Hetzner `/opt/chesscoach/`)

These are not GitHub secrets — they live on the server itself:

| Variable | Required | Description |
|----------|----------|-------------|
| `DOMAIN` | yes | Domain Caddy uses for TLS (e.g. `api.yourdomain.com`) |
| `GHCR_IMAGE` | yes | Full GHCR reference for the api container (e.g. `ghcr.io/owner/cereveon-llm-api:latest`); referenced by `docker-compose.prod.yml` |

All other backend variables (`SECA_API_KEY`, `SECRET_KEY`, `DATABASE_URL`,
`POSTGRES_*`, etc.) go into `/opt/chesscoach/.env.prod` — see section 1 and
`.env.prod.example` for the production-specific template.

### Weekly feedback digest

[`feedback-digest.yml`](../.github/workflows/feedback-digest.yml) runs every
Monday 06:37 UTC (plus `workflow_dispatch` for ad-hoc runs): it SSHes to the
Hetzner box with the same `HETZNER_HOST` / `HETZNER_SSH_KEY` pair the deploy
job uses, queries `feedback_messages` (the `POST /feedback` store, contract
§38) for rows from the last 7 days, and emails them to
`FEEDBACK_DIGEST_TO` via SMTP.

Operational properties:

- **Missing secrets fail the run loudly** — never warn-and-skip.  A digest
  that silently sends nothing is indistinguishable from "no feedback this
  week" (the failure mode a previous weekly cron actually shipped).
  GitHub's workflow-failure email is the liveness signal.
- **No feedback → no email.**  A quiet week is a green run with `count=0`;
  breakage is always red.
- **Feedback text never reaches the (public) workflow log** — the query
  step prints only the row count; message bodies travel exclusively in the
  email body.

---

## 7. First-Time Hetzner Bootstrap

Use the bootstrap script for initial server setup. CI handles all subsequent
deploys automatically.

### Prerequisites on the server

```bash
# Install Docker Engine + Compose plugin (Debian/Ubuntu)
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin curl python3

# Create deploy user and add to docker group
useradd -m -s /bin/bash deploy
usermod -aG docker deploy

# Create working directory and clone/copy the repo
mkdir -p /opt/chesscoach
cd /opt/chesscoach
# ... git clone or rsync the repo here ...
```

### Run the bootstrap script

```bash
cd /opt/chesscoach
./scripts/hetzner_setup.sh
```

The script:

1. Validates prerequisites and generates `.env.prod` from `.env.prod.example`
   (prompts you to fill in `SECA_API_KEY`, `POSTGRES_PASSWORD`, `DOMAIN`,
   `GHCR_IMAGE`, **`COACH_DEEPSEEK_API_KEY`**)
2. Pulls all GHCR images
3. Starts the full stack (`db`, `redis`, `api`, `caddy`)
4. Waits for the API health check and runs the smoke tests
5. Probes `GET /llm/health` and confirms DeepSeek connectivity

The script is idempotent — safe to re-run if interrupted.

### CORS for mobile clients

Android's `HttpURLConnection` does not send `Origin` headers, so CORS
restrictions are a browser-only concern. Set `CORS_ALLOWED_ORIGINS=*` in
`.env.prod` to silence the server warning and allow future web clients.
The `.env.prod.example` template already includes this value.

### Postgres schema initialization

`Base.metadata.create_all()` runs at server startup and creates all tables
from the SQLAlchemy models if they do not exist. No Alembic or manual
migration step is needed for a fresh deployment — the full schema
(including `player_embedding`) is created automatically by the first
`docker compose up`.

### After bootstrap

```bash
# Tail logs to confirm TLS cert provisioned
docker compose -f docker-compose.prod.yml logs -f caddy api

# Confirm DeepSeek is reachable from inside the api container
curl -s https://api.yourdomain.com/llm/health

# Register the first player (replace values)
curl -s -X POST https://api.yourdomain.com/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"changeme123"}'
```

---

## 8. References

- `.env.prod.example` — production environment template (copy to `.env.prod` on server)
- `.env.example` — dev/Docker variable reference with comments
- `scripts/hetzner_setup.sh` — first-time server bootstrap script
- `scripts/smoke_test.sh` — post-deploy health verification
- `docs/OPERATIONS.md` — runtime monitoring, telemetry, incident response
- `docs/ARCHITECTURE.md` — system design and layer boundaries
- `docs/API_CONTRACTS.md` — authoritative endpoint schemas
