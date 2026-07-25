OPERATIONS.md

ChessCoach-AI · Mode-2 Explanation System

1. Purpose of This Document

This document describes how to run, monitor, and maintain the ChessCoach-AI Mode-2 system in production.

It answers:

How the system is started

What must be monitored

What constitutes normal vs abnormal behavior

How regressions are detected

What to do when something breaks

This document does not describe:

Architecture (see ARCHITECTURE.md)

Testing philosophy (see TESTING.md)

Release process (see RELEASE.md)

2. Operational Scope

This document applies to:

Mode-2 explanation pipeline

Embedded deployment (rag.deploy.embedded)

Local / on-device / private backend usage

Source-available operation (see ../LICENSE.md)

It assumes:

No public API exposure

No multi-tenant environment

No user data persistence

3. System Startup
3.1 Environment Requirements

Required:

Python ≥ 3.11

DeepSeek API key (https://platform.deepseek.com)

Default model:

deepseek-v4-flash (DeepSeek-V4 non-thinking, OpenAI-compatible /chat/completions)

3.2 Required Environment Variables
COACH_DEEPSEEK_API_KEY=sk-...      # DeepSeek API key, required for live LLM
COACH_DEEPSEEK_API_BASE=https://api.deepseek.com   # optional override
COACH_DEEPSEEK_MODEL=deepseek-v4-flash  # optional override (default DeepSeek-V4 non-thinking)


If `COACH_DEEPSEEK_API_KEY` is missing, the system still serves but every
`/chat` call falls back to the deterministic template. `GET /llm/health`
surfaces the live LLM status so degraded mode is detectable in monitoring.

3.3 Starting the System (Embedded Mode)

From project root:

python -m uvicorn llm.server:app --host 0.0.0.0 --port 8000


Expected behavior:

No warnings

No silent failures

Either:

Valid explanation returned

Explicit exception raised

Silent output is not acceptable.

4. Runtime Execution Flow (Operational View)
Host App
  ↓
explain_position()
  ↓
extract_engine_signal()
  ↓
retrieve()          (RAG)
  ↓
render_mode_2_prompt()
  ↓
LLM.generate()
  ↓
validate_mode_2_negative()   ← hard gate
  ↓
score_explanation()          ← quality gate
  ↓
return explanation


Any failure stops execution immediately.

5. Failure Modes & Expected Responses
5.1 Validator Failure (Forbidden Patterns)

Example:

AssertionError: Mode-2 violation detected: pattern `checkmate`


Meaning

LLM violated a non-negotiable rule

Output was blocked correctly

Action

Do NOT weaken validator

Tighten system prompt if recurring

Inspect telemetry trend

5.2 Quality Score Failure

Example:

AssertionError: Explanation quality too low: 6/10


Meaning

Output was legal but insufficiently explanatory

Action

Acceptable in development

In production:

Either surface error

Or retry with same prompt (optional, bounded)

Do NOT auto-accept low-quality output.

5.3 LLM Runtime Errors (DeepSeek)

Examples:

HTTP 401 — invalid or revoked API key

HTTP 402 — billing exhausted / payment failed

HTTP 429 — rate limit exceeded

HTTP 5xx — DeepSeek upstream incident

Action

Treat as infrastructure error.  The chat_pipeline retry loop
(MAX_MODE_2_RETRIES) does not retry on these — instead the
exception is logged at WARNING level (chat_pipeline.py:557) and
the deterministic fallback in `_build_reply_deterministic` ships
to the user.  Users see template prose, never an HTTP error.

Confirm via `GET /llm/health` — the response carries the upstream
HTTP code and DeepSeek's error message in its `error` field, e.g.
`{"ok": false, "error": "HTTP 401: Authentication Fails, Your api key is invalid"}`.

For 401 / 402, rotate `COACH_DEEPSEEK_API_KEY` in `.env.prod` and
restart api: `docker compose -f docker-compose.prod.yml up -d --force-recreate api`.

For 429, check DeepSeek's [rate-limit dashboard](https://platform.deepseek.com).
The api falls back gracefully so users never see the limit; the
WARNING log lines are the operator signal.

5.4 Switching the LLM model (or provider)

Status

DeepSeek-V4 non-thinking (`deepseek-v4-flash`) is the production default.  Switching
to a different OpenAI-compatible model or provider is one env-var
change.

Procedure

1.  Update `COACH_DEEPSEEK_MODEL` in `.env.prod` to the new model
    string (e.g. `deepseek-reasoner` for chain-of-thought, or any
    OpenAI-compatible model name on a different gateway).
2.  If you're switching providers entirely (OpenAI, Together, Groq,
    self-hosted vLLM), also update `COACH_DEEPSEEK_API_BASE` to the
    new endpoint and `COACH_DEEPSEEK_API_KEY` to the new key.  The
    code talks pure OpenAI-compatible JSON so no Python changes
    are needed for any of these.
3.  `docker compose -f docker-compose.prod.yml up -d --force-recreate api`
4.  Probe `GET /llm/health` — should return `ok: true` within a
    few seconds.

Do NOT weaken the validators or the regression test to accommodate
a worse-behaved model — per CLAUDE.md rule #5, the failure is the
signal.

Telemetry to monitor

The retry-loop instrumentation in chat_pipeline (WARNING-level log
"Mode-2 LLM path failed" and DEBUG-level "Chat LLM blocked by
output firewall") is the operator window into how often production
hits the fallback path.  Spike in either log line = controllability
is degrading; flat or declining = current model is acceptable in
practice.  `docker compose -f docker-compose.prod.yml logs api -f | grep -i fallback`.

6. Telemetry Operations
6.1 What Is Collected

Stored in:

telemetry/quality_scores.jsonl


Each record contains:

timestamp

score (0–10)

case_type

model name

mode

No text, no prompts, no user data.

6.2 Normal Telemetry Profile

Healthy distribution:

Majority scores: 8–9

Some scores: 7

Rare scores: ≤6

Unhealthy indicators:

Mean score trending downward

Spike in scores exactly at 7

Repeated failures after model change

6.3 Telemetry Maintenance

File is append-only

Safe to delete between runs

Should be .gitignored

May be rotated manually

Telemetry must never influence runtime decisions.

7. Regression Detection

Regressions are detected via:

Negative golden tests

Positive golden tests

Prompt snapshot tests

Quality score thresholds

Telemetry trend analysis

If any regression test fails:

Stop deployment

Fix before release

8. CI Expectations (Operational)

CI must enforce:

All golden tests pass

Validators remain active

Prompt snapshots unchanged unless intentional

No test skips

CI must not:

Require a real LLM provider (DeepSeek API key, etc.)

Execute live LLM inference

Depend on telemetry

9. Model Upgrades (Operational Rules)

When changing LLM model:

Update LLM_MODEL

Run full golden test suite

Run local inference multiple times

Observe telemetry distribution

Only then promote model

Never upgrade models silently.

10. Incident Response Checklist

If something goes wrong:

Identify failure type

Confirm validator behavior

Check recent prompt changes

Inspect telemetry trends

Roll back last change if needed

Never bypass safety layers to “get output”.

11. Non-Goals (Explicit)

This system does NOT aim to:

Be creative

Provide best chess moves

Replace engine analysis

Optimize for verbosity

Personalize explanations

It aims to be:

Safe

Deterministic

Explainable

Regression-proof

12. Operational Invariant (Memorize This)

No output is always better than unsafe output.

If the system refuses to respond, that is a success condition, not a failure.

---

## 13. Observability Shipping to Grafana Cloud (Sprint 5.D.3)

The Hetzner backend emits JSON structured logs (Sprint 5.D.2) and a
Prometheus `/metrics` endpoint (Sprint 5.D.1).  Sprint 5.D.3 wires
**Grafana Alloy** as a sidecar to ship both to Grafana Cloud's free
tier — single vendor for logs + metrics, no inbound port exposure,
no ongoing cost.

The shipper is **opt-in** behind the `monitoring` compose profile.
The default deploy never starts the `alloy` container, so an operator
who hasn't yet set up Grafana Cloud sees zero impact.

### 13.1 First-time setup

1. **Create a Grafana Cloud account** at
   <https://grafana.com/products/cloud/>.  The free tier covers
   50 GB logs + Prometheus metrics + 14-day retention; no credit
   card required.

2. **Get the push endpoints + instance IDs.**  In Grafana Cloud,
   open **Connections → Data Sources** and find the pre-provisioned
   `grafanacloud-<stack>-logs` (Loki) and `grafanacloud-<stack>-prom`
   (Prometheus).  Each data source page shows:

   - The push URL under "Send data" (looks like
     `https://logs-prod-006.grafana.net/loki/api/v1/push` for Loki
     or `https://prometheus-prod-37-prod-eu-west-2.grafana.net/api/prom/push`
     for Prometheus).
   - A numeric **User** field — the per-data-source instance ID.
     Note these are **different** for Loki and Prometheus on the
     multi-tenant stack; copy each one separately.

3. **Generate ONE API key** at **Account → API Keys** with both
   `logs:write` AND `metrics:write` scopes.  Save the value;
   Grafana doesn't show it again.

4. **Set the env vars on the Hetzner host** by appending to
   `/opt/chesscoach/.env.prod`:

   ```
   GRAFANA_CLOUD_LOKI_URL=https://logs-prod-006.grafana.net/loki/api/v1/push
   GRAFANA_CLOUD_LOKI_USER=123456
   GRAFANA_CLOUD_PROM_URL=https://prometheus-prod-37-prod-eu-west-2.grafana.net/api/prom/push
   GRAFANA_CLOUD_PROM_USER=234567
   GRAFANA_CLOUD_API_KEY=glc_eyJ...long_token...
   ```

   See `.env.example` for the full block of inline comments.

5. **Start the alloy service**:

   ```bash
   cd /opt/chesscoach
   docker compose --profile monitoring up -d alloy
   ```

   The `monitoring` profile is required — without it, `docker compose up`
   skips the alloy service entirely.

### 13.2 Verify ingestion

1. **Logs**: in Grafana Cloud → **Explore → Loki**, run
   ```
   {job="cereveon-backend"} | json | level="INFO"
   ```
   Expect to see request-completion lines from `llm.server` within
   ~30 seconds of starting Alloy.

2. **Metrics**: in Grafana Cloud → **Explore → Prometheus**, query
   ```
   chesscoach_http_requests_total{job="cereveon-backend"}
   ```
   Expect non-zero counters keyed by `method`, `path_template`,
   `status`.  Add `request_id` filtering in Loki to cross-reference
   a specific request's log lines with its corresponding metric
   labels.

### 13.3 Stopping the shipper

```bash
docker compose --profile monitoring stop alloy
```

Logs continue to land in Docker's local `json-file` driver
(50 MB × 5 file rotation per service) — only the outbound shipping
pauses.  No data is lost on the host; restarting Alloy resumes from
the last tail position via the persisted `alloy_data` volume.

### 13.4 Disabling permanently

Remove the five Grafana Cloud env vars from `.env.prod`.  The next
`docker compose up` without the `monitoring` profile leaves the
alloy service stopped.  The named volume `alloy_data` can be safely
removed: `docker volume rm chesscoach_alloy_data` once the service
is down.

### 13.5 Cost-control notes

- Free-tier ceiling is 50 GB logs/month.  At the current request
  volume (request-completion INFO log per call + the discovery
  routes) we're nowhere near it.  Watch `chesscoach_http_requests_total`
  in Grafana for budget pressure.
- The `request_id` label is high-cardinality (one per request).  If
  retention pressure becomes a problem, drop the label by editing
  the `stage.labels` block in `monitoring/alloy.alloy` — the
  field remains queryable as a JSON line filter (`| json | request_id="..."`),
  it just stops being indexed.
- `/metrics` is scraped at a fixed 30-second interval; this is the
  Prometheus default and matches Grafana Cloud's free-tier minimum.

End of OPERATIONS.md