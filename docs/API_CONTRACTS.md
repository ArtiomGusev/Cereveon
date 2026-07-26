# API Contracts

Authoritative schema contracts for the Chess Coach backend API.
Derived from the production implementation; any deviation constitutes a
**contract mismatch** and must be caught by `test_api_contract_validation.py`.

---

## Conventions

- All endpoints use `Content-Type: application/json`.
- Auth-required endpoints expect `X-Api-Key: <key>` (server.py routes) or
  `Authorization: Bearer <token>` (SECA routes).
- `null` values are allowed for optional fields unless stated otherwise.
- `_metrics` is an internal diagnostic field; clients MUST NOT treat it as
  part of the stable contract (its shape varies by cache source).

---

## 1. `POST /engine/eval`

**Host:** `server.py`
**Auth:** `X-Api-Key` required
**Rate limit:** 30 / minute

Migrated from `host_app.py` in the host_app retirement pass.  Contract narrowed
during the migration — see "Removed in 2026-05-12" below.

### Request body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `fen` | `string` | yes | Full 6-field FEN.  Validated server-side via `chess.Board(...)`; invalid input returns 400. |

### Response

```json
{
  "score":     <int | null>,
  "best_move": <string | null>,
  "source":    <"engine" | "unavailable">
}
```

| Field | Type | Notes |
|-------|------|-------|
| `score` | `int \| null` | Centipawns from White's perspective. Positive = White better. Mate is reported as `±10000`. `null` when the engine pool is unavailable. |
| `best_move` | `string \| null` | Best move in UCI notation (e.g. `"e2e4"`). `null` when no legal moves or engine unavailable. |
| `source` | `string` | `"engine"` on the happy path; `"unavailable"` when the Stockfish pool is down or saturated (the route degrades to a 200-with-nulls rather than 500 to match the Android client's `engineAvailable=false` fallback in `ChessViewModel.dispatchEngineEval`). |

### Removed in 2026-05-12 (host_app retirement)

The pre-migration contract supported a `GET /engine/eval` variant and
`moves` / `movetime_ms` / `nodes` body fields.  None of these were used by
any in-tree caller (the Android `HttpEngineEvalClient` only POSTs `{"fen": ...}`),
and `host_app.py` was never actually deployed to production (the legacy
`llm/Dockerfile` that ran it was orphaned years before the
`llm/Dockerfile.api` split).  The simpler contract above matches what the
Android client actually sends; adding any of the removed fields back is a
contract widening that requires an Android client update in the same release.

---

## 2. `GET /next-training/{player_id}` — RETIRED in PR 26

The endpoint was a placeholder implementation with hardcoded "demo
weaknesses" that never advanced past the comment in the source.
Android always called `POST /curriculum/next` first (the SECA-driven
authoritative path); `/next-training` was the fallback that ran when
`/curriculum/next` failed — but the fallback was showing fake-data
recommendations, not a real signal.  Retired in PR 26 (2026-05-15)
alongside the Android-side `getNextTraining` method +
`TrainingRecommendation` DTO.  See `§18 POST /curriculum/next` for
the surviving training-recommendation contract.

---

## 3. `POST /game/finish`

**Host:** `llm/seca/events/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Route prefix:** `/game`

### Request body

```json
{
  "pgn":        <string>,
  "result":     <"win" | "loss" | "draw">,
  "accuracy":   <float 0..1>,
  "weaknesses": <object: {string: float}>,
  "player_id":  <string | null>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `pgn` | `string` | Non-empty, ≤ 100 000 chars. **Authoritative trust input** — the server re-analyses this PGN with the engine pool to derive `accuracy` + `weaknesses` server-side (PR #142, `seca/analysis/pgn_accuracy.py`). |
| `result` | `string` | Exactly one of `"win"`, `"loss"`, `"draw"` |
| `accuracy` | `float` | 0.0 ≤ value ≤ 1.0. **Accepted but server-side recompute is authoritative** — the client value is used as a fallback only when the engine pool is unavailable or the PGN can't be parsed (logged as `ACC_FALLBACK` server-side). A modded client cannot inflate the bandit's reward signal by sending `accuracy=1.0`. |
| `weaknesses` | `object` | ≤ 50 keys; values are numeric. Same authority model as `accuracy` — server-side recompute overrides. |
| `player_id` | `string \| null` | If provided, must match authenticated player |

### Response

```json
{
  "status":     "stored",
  "new_rating": <float>,
  "confidence": <float>,
  "learning":   <object>,
  "coach_action": {
    "type":     <string>,
    "weakness": <string | null>,
    "reason":   <string>
  },
  "coach_content": {
    "title":       <string>,
    "description": <string>,
    "payload":     <object>
  },
  "analysis": {
    "dominant_category":  <string | null>,
    "games_analyzed":     <int>,
    "recommendations":    [...]
  },
  "biggest_mistake": null | {
    "fen":           <string>,
    "played_move":   <UCI string>,
    "move_number":   <int>,
    "eval_loss_cp":  <int>,
    "source_ref":    <string>
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `status` | `string` | Always `"stored"` on success |
| `new_rating` | `float` | Updated player rating |
| `confidence` | `float` | Updated player confidence |
| `learning` | `object` | Contains `{"status": <string>}` |
| `coach_action.type` | `string` | One of: `"NONE"`, `"REFLECT"`, `"DRILL"`, `"PUZZLE"`, `"PLAN_UPDATE"` |
| `coach_action.weakness` | `string \| null` | Weakness name when type is `DRILL` or `PLAN_UPDATE` |
| `coach_action.reason` | `string` | Human-readable decision reason |
| `coach_content.title` | `string` | Content title shown to player |
| `coach_content.description` | `string` | Content description |
| `coach_content.payload` | `object` | Type-specific content payload |
| `biggest_mistake` | `object \| null` | The player's **first** move whose centipawn loss clears `MIN_MISTAKE_LOSS_CP` (150 cp), or `null` when (a) the engine recompute fell back to client values, or (b) no move clears the threshold.  Drives the Android Phase-3 mistake-replay sheet.  Selection policy is "first above threshold" (not "largest loss") so the player learns the originating mistake before its downstream cascade — the wire field name is retained from PR #192's original "biggest loss" picker for backward compatibility with the Android decoder. |
| `biggest_mistake.fen` | `string` | FEN of the position **before** the bad move. |
| `biggest_mistake.played_move` | `string` | UCI of the move the player actually played at that position. |
| `biggest_mistake.move_number` | `int` | 1-indexed Nth player half-move (not Nth ply).  Used in the replay sheet header copy. |
| `biggest_mistake.eval_loss_cp` | `int` | Centipawn loss this move cost the player. Always ≥ 150 when the field is populated. |
| `biggest_mistake.source_ref` | `string` | Opaque identifier to pass back to `POST /training/solve` on a verified-correct replay so dedup works (`event_<event_id>:move_<n>`). |

---

## 4. `POST /live/move`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>` (route depends on
`get_current_player`, so JWT is required alongside the API key; absent or
invalid Bearer returns 401)

### Request body

```json
{
  "fen":        <string>,
  "uci":        <string>,
  "player_id":  <string | null>,
  "fen_before": <string | null>,
  "game_id":    <string | null>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `fen` | `string` | Valid FEN string; non-empty. Position **after** the move. |
| `uci` | `string` | UCI move (4–5 chars, e.g. `"e2e4"`, `"e7e8q"`) |
| `player_id` | `string \| null` | Optional player identifier |
| `fen_before` | `string \| null` | Optional — position **before** the move. When present (and `fen_before` + `uci` actually reaches `fen`, an integrity check), the server runs a second Stockfish eval on it and grades move quality from the centipawn swing `fen_before → fen`, surfaced as `engine_signal.last_move_quality` / `move_quality`. The server can't reconstruct the pre-move position from `fen` alone (a capture / en-passant / promotion loses the captured piece), so the client supplies it. Absent/null → `move_quality` stays `"unknown"` (pre-feature behaviour). Validated through the same FEN gate as `fen`. Additive + backward-compatible (no `X-API-Version` bump). |
| `game_id` | `string \| null` | Optional distinct-game key for the free-tier entitlements admission (`llm/seca/entitlements`). Same validator posture as `ChatRequest.game_id`: ≤64 chars, empty/whitespace → `null`. When enforcement is ON (`SECA_ENTITLEMENTS_ENFORCED`), the plan's daily quota of **distinct** `game_id`s keeps the LLM-coached path; an over-quota game answers every move via the deterministic coach instead (see `coach_tier` below) — never an error. Absent/null → the admission check **fails open** (older clients keep the LLM path unconditionally). Additive + backward-compatible (no `X-API-Version` bump). |

### Response

```json
{
  "status":             "ok",
  "hint":               <string>,
  "engine_signal":      <object>,
  "move_quality":       <string>,
  "mode":               "LIVE_V1",
  "coach_tier":         {"plan": <string>, "degraded": <bool>, "remaining": <int | null>}
}
```

| Field | Type | Notes |
|-------|------|-------|
| `status` | `string` | Always `"ok"` on success |
| `hint` | `string` | Human-readable coaching hint; may be empty string `""` |
| `engine_signal` | `object` | Structured evaluation context (see `EngineSignalDto`) |
| `move_quality` | `string` | Quality label: `"good"`, `"inaccuracy"`, `"mistake"`, `"blunder"` |
| `mode` | `string` | Always `"LIVE_V1"` for this endpoint |
| `coach_tier` | `object` | Additive (2026-07, freemium Subtask 3; `LiveMoveResponse` ignores unknown keys, so old clients are unaffected — same leniency that covered the `dynamic_adaptation` retirement). `plan` = `"free"` / `"pro"`. `degraded` = `true` when this hint came from the deterministic coach because the game is over the plan's daily coached-game quota — the client should render its upgrade/limit chip. `remaining` = distinct coached games left today, or `null` while metering is dormant (`null` means "not metered", distinct from `0`). Engine analysis (`engine_signal`, `move_quality`) is unaffected by degradation — only the hint generator changes. |

The previous response carried a `dynamic_adaptation` boolean from the
in-process `_dynamic_registry`. That registry + its `/adaptation/mode`
control surface + the related `/move` endpoint were retired in PR 23
(2026-05-15) after the SECA-Android wiring audit confirmed no Android
caller had ever exercised any of them. The `validate_live_move_response`
Pydantic schema was already lenient to the extra field, so the removal
is wire-backward-compatible with any unknown client.

### Notes
- `hint` must be preserved as-is by clients even when empty; clients must not
  substitute `null` for an empty string.
- Tested end-to-end by `LiveMoveApiClientIntegrationTest` (Android) and
  `test_live_move_pipeline.py` (backend).

---

## 5. `POST /chat`

**Host:** `server.py`
**Auth:** `X-Api-Key` required

### Request body

```json
{
  "fen":            <string>,
  "messages":       <array of {role, content}>,
  "player_profile": <object | null>,
  "past_mistakes":  <string[] | null>,
  "move_count":     <int | null>,
  "coach_voice":    <string | null>,
  "game_id":        <string | null>,
  "last_move":      <string | null>,
  "player_color":   <string | null>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `fen` | `string` | Valid FEN or `"startpos"` |
| `messages` | `array` | ≤ 50 turns. `user`-turn content ≤ 2000 chars (422 above). `assistant`-turn content over 2000 chars is **truncated server-side**, not rejected: the coach's own replies are budgeted at 500 completion tokens (≈ up to ~2500 chars), so a legitimate reply can exceed the cap — rejecting it on history replay would 422 every later `/chat` call in the thread ("Coach is offline" wedge). History is prompt context only. |
| `player_profile` | `object \| null` | Optional — keys: `skill_estimate`, `common_mistakes`, `strengths` |
| `past_mistakes` | `string[] \| null` | Optional — ≤ 20 items |
| `move_count` | `int \| null` | Optional — 0–10 000; injects "This is move N of the game." into the context block |
| `coach_voice` | `string \| null` | Optional tone setting. Allow-list: `"formal"`, `"conversational"`, `"terse"` (case-insensitive, whitespace-stripped; empty string is coerced to `null`). Unknown values reject the request with 422. Default `null` → server treats as `"conversational"`. Affects tone only; engine truth and validator gates are unchanged. Pinned by `test_chat_coach_voice.py`. |
| `game_id` | `string \| null` | Optional — per-game chat thread key (the client's current `games.id`). When present, the saved exchange is scoped to that game so `GET /chat/history?game_id=…` shows only that game's chat; absent/null keeps it player-global (legacy). ≤ 64 chars; empty → `null`. `player_id` (from the JWT) stays the isolation boundary, so this is an organizational key only. Same field on `POST /chat/stream`. |
| `last_move` | `string \| null` | Optional — the player's most recent move in UCI (`[a-h][1-8][a-h][1-8]` + optional promotion `[qrbnQRBN]`). Lets the coach describe it in plain English ("you advanced your f-pawn") instead of misreading the raw FEN; the server renders it coordinate-free via `describe_move_plain` so the no-notation output rule isn't tripped. Absent/null → no move line. Same field on `POST /chat/stream`. 422 on malformed UCI. |
| `player_color` | `string \| null` | Optional — the colour the player is playing, for the coach's "you" framing. Allow-list: `"white"`, `"black"`; anything else rejects with 422. Absent/null → `"white"` (the pre-feature anchor; live in-app games are always White). Clients send `"black"` when chat is opened on an imported/replayed game where the user played Black (the review board orients to the player's side). Flips the prompt perspective block, the ENGINE FACTS "you"/"your opponent" phrasing, and the deterministic fallback's mate framing. Additive and backward-compatible — no `X-API-Version` bump. Same field on `POST /chat/stream`. |

### Response

```json
{
  "reply":         <string>,
  "engine_signal": <object>,
  "mode":          "CHAT_V1"
}
```

### Errors

- `402` — chat quota exhausted (entitlements; only when
  `SECA_ENTITLEMENTS_ENFORCED` is on — dormant otherwise). Shape B body
  with metering extras, emitted before any engine/LLM work:

  ```json
  {"error": "chat_daily_limit", "plan": "free", "limit": 3, "used": 3,
   "reset_at": "2026-07-18T14:03:22.511000",
   "upgrade": {"product": "pro_monthly"}}
  ```

  `plan` / `limit` / `used` reflect the caller's plan (`free` = 3 per
  rolling 24h, `pro` = 30 per rolling 24h anti-abuse rail — ~10× honest
  heavy use; lowered from 100 on 2026-07-06 to halve the pathological
  per-subscriber token ceiling). The chat quota is a **rolling 24h
  window anchored at the first turn in the window**, NOT a UTC-calendar
  day (changed 2026-07-22). `reset_at` is when the window frees up:
  ISO-8601, UTC, no offset suffix (the oldest in-window turn + 24h);
  the client renders a live "resets in Xh Ym" countdown from it. The
  `chat_daily_limit` discriminator string is kept for wire compatibility
  even though the window is no longer calendar-daily. Non-2xx, so no
  `X-Auth-Token` rotation header ships (the presented JWT stays valid —
  see §10). The turn is **not** consumed. Clients render this as the
  upgrade/paywall surface.
  A successful reply consumes one turn at the same 2xx side-effect
  boundary as history persistence, so server errors never eat quota.

---

## 6. `POST /seca/explain`

**Host:** `llm/server.py` (SECA inference router, prefix `/seca`)
**Auth:** `X-Api-Key` required

### Request body

```json
{
  "fen":       <string>,
  "player_id": <string>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `fen` | `string` | Valid FEN for the current position |
| `player_id` | `string` | Identifies the player for skill tracking |

### Response

```json
{
  "explanation": <string>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `explanation` | `string` | Coach explanation generated by the SECA pipeline |

### Notes
- Runs the full SECA pipeline: engine analysis → RAG doc retrieval → LLM explanation
  → skill update → telemetry.
- Distinct from `POST /explain` (root-level, `SAFE_V1` mode) which uses a
  deterministic safe-explainer without the SECA pipeline.

---

## 6b. `/coach` — NOT IMPLEMENTED

The `/coach` endpoint does not exist. Coaching decisions are embedded in
the `POST /game/finish` response (`coach_action` + `coach_content` fields).

Any client expecting a standalone `/coach` endpoint will receive HTTP 404.

---

## 7. `GET /game/history`

**Host:** `llm/seca/events/router.py`
**Auth:** `Authorization: Bearer <token>` required

### Query parameters

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `source` | `"app" \| "lichess"` | *(omitted)* | Filter by provenance. `lichess` = games pulled by the Lichess import service; `app` = in-app games, **including legacy NULL-source rows** (in-app was the only writer before imports existed). Omit for all sources intermixed by recency. Any other value → **422** (enforced by the endpoint's `^(app\|lichess)$` pattern). |
| `limit` | `int` | `20` | `1 ≤ limit ≤ 100`. Newest-first. Default 20 preserves the exact pre-2026-07-03 behaviour for clients that send no `limit`; the Android In-app / Lichess tabs request more so a filtered view isn't truncated by unrelated recent games. |

### Response

```json
{
  "games": [
    {
      "id":           <string>,
      "game_id":      <string | null>,
      "source":       <"app" | "lichess">,
      "last_move":    <string | null>,
      "winner_move":  <string | null>,
      "result":       <"win" | "loss" | "draw">,
      "accuracy":     <float 0..1>,
      "created_at":   <string | null>,
      "rating_after": <float | null>
    }
  ]
}
```

| Field | Type | Notes |
|-------|------|-------|
| `games` | `array` | Up to `limit` entries (default 20), ordered newest-first |
| `id` | `string` | Game event UUID (the `game_events` row id) |
| `game_id` | `string \| null` | Live game id (the `games.id` from `POST /game/start`, equal to `chat_turns.game_id`). Pass to `GET /chat/history?game_id=…` to load this game's coaching chat. `null` for legacy rows, imported (e.g. Lichess) games, and finishes from clients that didn't send a `game_id` — those have no per-game chat thread. |
| `source` *(2026-07-03)* | `string` | Provenance: `"lichess"` for imported games, `"app"` for in-app games. **Legacy NULL-source rows normalise to `"app"`** so the client always receives a concrete label. Additive field — older clients ignore it (`ignoreUnknownKeys`). |
| `last_move` | `string \| null` | SAN of the final mainline move (e.g. `"Nc6"`, `"Qxh7#"`), derived server-side from the stored PGN, so the history list can preview how each game ended. `null` for moveless or unparseable / legacy PGN. |
| `winner_move` | `string \| null` | SAN of the **winning side's** final mainline move, per the PGN `Result` header (`1-0` = White, `0-1` = Black). Differs from `last_move` when the loser made the last move on the board. `null` for draws, ongoing / unknown results, moveless or unparseable PGN. |
| `result` | `string` | One of `"win"`, `"loss"`, `"draw"` |
| `accuracy` | `float` | 0.0–1.0. For imported Lichess games this is `0.0` until the post-import engine-analysis pass (§31) scores the game, then the engine-derived value. |
| `created_at` | `string \| null` | ISO-8601 datetime string. **For imported games this is the import time**, not the game's original play date, so a fresh import clusters at the top of the unfiltered list. |
| `rating_after` | `float \| null` | Rating after this game; `null` if no rating update was stored — always `null` for imported Lichess games (the importer writes no `RatingUpdate`). |

---

## 7a. `GET /game/{event_id}/positions`

**Host:** `llm/seca/events/router.py`
**Auth:** `Authorization: Bearer <token>` required

Per-ply board positions for replaying a finished game in the history "review"
screen. Derived server-side from the stored `GameEvent.pgn` (python-chess) —
the client never parses PGN. Keyed by `event_id` (the `id` from
`GET /game/history`), so it works for every game, including legacy rows with no
chat thread.

### Response

```json
{
  "positions":    ["<fen>", "…"],
  "moves":        ["<san>", "…"],
  "player_color": <"white" | "black" | null>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `positions` | `array<string>` | N+1 FENs: index 0 is the start, index *i* is the board after ply *i* (`positions` last entry is the final position) |
| `moves` | `array<string>` | N SANs: `moves[i]` produced `positions[i+1]` (e.g. `"e4"`, `"Nf3"`); for move-list labels |
| `player_color` *(2026-07-06)* | `string \| null` | Which side the player was on, for review **board orientation**. `"white"` / `"black"` for imported Lichess games (the importer derives it by matching the linked handle against the game's players and stores it on the row). Rows imported *before* this column existed have no stored value; the endpoint then falls back to matching the player's current linked Lichess handle against the PGN's `White`/`Black` headers at read time. `null` for in-app games (always played as White), and for imported rows that aren't derivable (no current link / no header match) — the client renders `null` as White (no flip). Additive field; older clients ignore it. |

Status codes: `400` (`event_id` over the 64-char cap), `403` (not the owner), `404` (unknown event).

---

## 8. `POST /auth/change-password`

**Host:** `llm/seca/auth/router.py`
**Auth:** `Authorization: Bearer <token>` required

### Request body

```json
{
  "current_password": <string>,
  "new_password":     <string>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `current_password` | `string` | Must match the stored hash |
| `new_password` | `string` | Minimum 8 characters |

### Response

```json
{ "status": "updated" }
```

| HTTP Status | Meaning |
|-------------|---------|
| 200 | Password updated successfully |
| 400 | `current_password` does not match (`"Current password is incorrect"`) or `new_password` too short |
| 401 | Invalid or expired token |

---

## 9. `POST /game/coach-feedback`

**Host:** `llm/seca/events/router.py`
**Auth:** `Authorization: Bearer <token>` required

### Request body

```json
{
  "session_fen": <string>,
  "is_helpful":  <boolean>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `session_fen` | `string` | FEN of the board position when feedback was given |
| `is_helpful` | `boolean` | `true` = thumbs up, `false` = thumbs down |

### Response

```json
{ "status": "recorded" }
```

Feedback is logged server-side. This endpoint is fire-and-forget; clients
should not block the UI on the result.

---

## 10. `GET /auth/me` / `PATCH /auth/me`

**Host:** `llm/seca/auth/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

### `GET /auth/me`

Returns the authenticated player's profile + skill vector.

#### Response

```json
{
  "id":           <string>,
  "email":        <string>,
  "rating":       <float>,
  "confidence":   <float>,
  "skill_vector": { "<skill>": <float>, ... },
  "training_xp":  <int>
}
```

`training_xp` is a monotonic counter incremented when the player completes a
training exercise (the seed = replay of an engine-flagged mistake from their
own game; the derivatives = weekly micro-tasks of the same mistake pattern in
new positions).  The Android client renders it as a Level/XP card on the Home
screen; `rating` and `confidence` are still returned but are no longer shown
to the user — they continue to drive adaptive opponent selection internally.
Defaults to `0` for legacy rows.

#### Side effect — cold-start Lichess backfill *(2026-07-03)*

`GET /auth/me` is the Android client's launch-time profile sync (both
`HomeActivity` and `MainActivity` call it at cold start).  As a best-effort
side effect it runs a **one-time Lichess history backfill**: if the
authenticated player has a `/lichess` link but **zero** imported
(`source='lichess'`) games, it kicks the same v2 import job §16a starts on
sign-in.  This covers accounts that linked Lichess *before* auto-import-on-sign-in
shipped — their history flows in on the next app open, with no re-sign-in and
no client change.  Gated on zero imported games, so it fires at most once per
account (coalescing prevents duplicate jobs in the pre-first-row window).  The
backfill never affects the response: `/auth/me` returns the profile above
regardless of import outcome.

### `PATCH /auth/me`

Partial profile update — used by the Onboarding flow + the Settings
"Skill rating" affordance to forward calibration to the server.

#### Request body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `rating`     | `float \| null` | no | Bounds: `(0, 4000]`. |
| `confidence` | `float \| null` | no | Bounds: `[0.0, 1.0]`. |

At least one field must be non-null (empty body returns 400).

#### Wire shape (Android client)

The JDK's HttpURLConnection rejects PATCH on JDK 17, so the Android
client sends `POST /auth/me` + `X-HTTP-Method-Override: PATCH` —
the server's `http_method_override` middleware promotes it.

#### Response

Same shape as `GET /auth/me` (post-update values).

### `X-Auth-Token` refresh header

**Both endpoints** (and every other authenticated endpoint that
depends on `get_current_player`) include a `X-Auth-Token` response
header with a freshly-minted JWT bound to the same `session_id`.
Active clients rotate their stored token via this header so the JWT
exp can stay tight (24h) without bouncing active users.

Failure paths (401 / 403 / 422 / 500) do NOT emit `X-Auth-Token` —
defends against a hostile client harvesting tokens by probing.

---

## 11. `POST /game/start`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>`

Creates a new in-progress row in the `games` table.  `player_id` is
derived from the JWT; any value in the request body is ignored.

### Request body

```json
{ "player_id": <string>  // legacy field, ignored }
```

### Response

```json
{ "game_id": <string> }   // UUID
```

Pair with `POST /game/finish` (see §3) and `POST /game/{game_id}/checkpoint`
(see §12) to close the lifecycle properly.

### Errors

- `402` — free-tier game limit reached (entitlements; only when
  `SECA_ENTITLEMENTS_ENFORCED` is on — dormant otherwise). The free tier
  is **1 coached game per rolling 24h, hard-blocked**: once the player
  has played their game (made a move in it — the coached-game admission
  marker is written on the first `/live/move`), a new `/game/start` is
  refused. Shape B body, same envelope as the chat 402 (§5) with a
  distinct `error` discriminator:

  ```json
  {"error": "game_daily_limit", "plan": "free", "limit": 1, "used": 1,
   "reset_at": "2026-07-18T14:03:22.511000",
   "upgrade": {"product": "pro_monthly"}}
  ```

  The client renders this as a non-dismissible paywall with a live
  "resets in Xh Ym" countdown driven by `reset_at`, and does **not**
  enter a game — the server returned no `game_id`. The free game limit
  is a **rolling 24h window anchored at the played game**, NOT a
  UTC-calendar day (changed 2026-07-22); `reset_at` (ISO-8601, UTC, no
  offset suffix) = the blocking game's admission time + 24h. The
  `game_daily_limit` discriminator is kept for wire compatibility.
  Starting or resetting a game **before** the first move is not blocked
  (`remaining >= 1`): a misclick or opening rethink costs nothing.
  Resuming an existing game does not call `/game/start` and is never
  gated. `pro` is **never** hard-blocked here — the paywall sells
  "Unlimited adaptive games", so a subscriber always gets a `game_id`;
  past pro's coached-game cap (10 per rolling 24h) the `/live/move`
  admission degrades hints to the deterministic coach instead
  (`coach_tier.degraded`, §4), which is what caps token spend. Non-2xx,
  so no `X-Auth-Token` rotation header ships.

---

## 12. `POST /game/{game_id}/checkpoint`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>`

Persists the in-progress board state for cross-device resume.  Called
by the Android client after every move.

### Path params

| Field     | Type     | Notes                                          |
|-----------|----------|------------------------------------------------|
| `game_id` | `string` | Max 64 chars, no control chars.  Returned by `POST /game/start`. |

### Request body

| Field         | Type     | Required | Description |
|---------------|----------|----------|-------------|
| `fen`         | `string` | yes      | Full FEN of the current position.  Max 256 chars. |
| `uci_history` | `string` | no       | Comma-separated UCI moves (e.g. `"e2e4,e7e5"`).  Max 16 384 chars; defaults to `""`. |

### Response

```json
{ "status": "checkpointed" }
```

### Failure modes

| Status | Meaning |
|--------|---------|
| 400    | `game_id` too long or contains control chars; FEN/uci_history invalid |
| 403    | Game belongs to another player |
| 404    | `game_id` doesn't exist |
| 409    | Game is already finished (cannot checkpoint a closed game) |

---

## 13. `GET /game/active`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>`

Returns the player's most-recent unfinished game with a checkpoint —
the cross-device resume backbone.  Called by `HomeActivity` cold-start
when no local SharedPreferences snapshot exists (fresh install /
device swap).

### Response (200)

```json
{
  "game_id":             <string>,
  "current_fen":         <string>,
  "current_uci_history": <string>,
  "last_checkpoint_at":  <string>,   // ISO timestamp
  "started_at":          <string>    // ISO timestamp
}
```

### Failure modes

| Status | Meaning |
|--------|---------|
| 404    | No resumable game (no unfinished game with a checkpoint).  Treated as **absence-of-data, not error** by the Android client — `getActiveGame()` returns `Success(null)`. |

---

## 14. `GET /repertoire`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>`

Backs the AtriumOpenings screen.  Returns the player's saved
opening lines, or a canonical 4-entry default list when nothing is
stored (defaults are NOT persisted on read — GET stays
side-effect-free).

### Response

```json
{
  "openings": [
    {
      "eco":       <string>,    // e.g. "C84"
      "name":      <string>,    // e.g. "Ruy Lopez · Closed"
      "line":      <string>,    // e.g. "1.e4 e5 2.♘f3 ♘c6 3.♗b5 a6"
      "mastery":   <float>,     // 0.0–1.0
      "is_active": <bool>,      // exactly one entry should be true
      "ordinal":   <int>        // display order (lower = first)
    },
    ...
  ]
}
```

### Notes

- Default ECOs (`C84`, `B22`, `D02`, `A04`) originally mirrored the
  Android client's `OpeningsActivity.DEFAULT_REPERTOIRE`; that screen was
  removed from the UI (the endpoint and its client methods survive for a
  future repertoire surface), so the server list is now the single source
  of the defaults.  A pin test
  (`test_repertoire_endpoint.py::test_default_mirrors_android_companion`)
  keeps first-vs-subsequent visits from showing different defaults.
- Edit endpoints (add / delete / set-active / drill-result) are documented
  in §23–§26 below.  All four seed the default repertoire on first write
  so a user editing one of the canonical lines materialises a persistent
  copy on the same call.

---

## 15. `POST /auth/register`

**Host:** `llm/seca/auth/router.py`
**Auth:** none (creates the credential)
**Rate limit:** 5 / minute

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `email` | `string` | Must pass `_validate_email_strict`. Lower-cased server-side. |
| `password` | `string` | ≤ 1000 chars. |

### Response

```json
{
  "access_token": <string>,
  "player_id":    <string>,
  "token_type":   "bearer"
}
```

### Errors

- `400` — registration failed (duplicate email or invalid email format).
- `429` — rate limit (5 / min per client IP).

---

## 16. `POST /auth/login`

**Host:** `llm/seca/auth/router.py`
**Auth:** none (consumes credentials)
**Rate limit:** 10 / minute

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `email` | `string` | See §15. |
| `password` | `string` | ≤ 1000 chars. |
| `device_info` | `string` | Optional, ≤ 200 chars, no control characters. Recorded against the session row for the upcoming device-list UI; not used for auth decisions today. |

### Response

Same shape as §15.

### Errors

- `401` — invalid credentials (uses constant-time compare server-side, pinned by `test_security_new_findings.py::SN_01`).
- `429` — rate limit.

---

## 16a. `POST /auth/lichess`

**Host:** `llm/seca/auth/router.py`
**Auth:** none (consumes an OAuth authorization code)
**Rate limit:** 10 / minute

"Sign in with Lichess" — OAuth 2.0 authorization-code + PKCE (RFC 7636).
The Android client runs the authorization step in the system browser
(`LichessOAuth.kt` builds the `https://lichess.org/oauth` URL; the
redirect lands on `ai.chesscoach.app://lichess-auth`), then forwards the
one-time `code` + its `code_verifier` here.  The **server** performs the
code exchange and account fetch
(`llm/seca/lichess/client.py::exchange_authorization_code` /
`fetch_account`), so Lichess access tokens never live on the device and a
token minted for a different app cannot be replayed into a sign-in.  The
pinned OAuth identifiers (`client_id = ai.chesscoach.app`, `redirect_uri =
ai.chesscoach.app://lichess-auth`) must byte-match between
`LichessOAuth.kt` and `llm/seca/lichess/client.py`; Lichess accepts
unregistered public clients, so no upstream registration exists to catch a
drift.  No scopes are requested — public identity only.

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `code` | `string` | 1–512 printable-ASCII chars (opaque Lichess authorization code; single-use). |
| `code_verifier` | `string` | RFC 7636 §4.1 shape: 43–128 chars of `[A-Za-z0-9\-._~]`. |
| `device_info` | `string` | Optional, ≤ 200 chars, no control characters — same rules as §16. |

### Response

```json
{
  "access_token":     <string>,
  "player_id":        <string>,
  "token_type":       "bearer",
  "created":          <bool>,     // true when this sign-in created the account
  "lichess_username": <string>    // display-cased handle from /api/account
}
```

Superset of the §15/§16 shape — the Android client deserialises it as
`LoginResponse` (`ignoreUnknownKeys`).

### Semantics

- The player row is keyed on `players.lichess_user_id` (the canonical
  lowercase id from `GET /api/account`, shape-validated fail-closed).
- First sign-in creates the account with a synthetic
  `email = "lichess:<id>"` — outside the reachable email space (`_EMAIL_RE`
  rejects it at §15/§16, so the namespace cannot be squatted;
  `test_auth_lichess.py::LI_10`) — and an unusable random password hash.
- The Lichess access token is revoked (best-effort `DELETE /api/token`)
  immediately after the account fetch — including when the fetch itself
  fails after a successful exchange, so no live token is left dangling.
- Best-effort auto-link: when the player has no `/lichess` link yet, the
  game-import link (§27) + first-link calibration are created from the
  already-fetched account profile.  Link failure never fails the sign-in,
  and *this* player's existing link is never modified.  When the handle is
  linked to a **different** Cereveon account, OAuth sign-in **claims** it
  (*2026-07-03*): verified OAuth ownership overrides another account's
  self-asserted `/lichess/link`.  As of *2026-07-17* the manual
  `/lichess/link` route (§27) is itself OAuth-verified and sets the same
  `claim_from_other_player` flag — so it too **claims** a contested
  handle rather than returning 409 (there is no longer an unverified
  link path that could create a contested handle in the first place).
  The losing account's active import jobs are cancelled and its link row
  removed; its imported games remain as history.  This resolves the
  same-human / two-logins case (handle linked on a password account,
  OAuth sign-in on another that could otherwise never link).
- Best-effort auto-import *(2026-07-03)*: after the link step, the same
  v2 background job as §31 is started (`max_games=50`, **rated + casual**,
  watermark-incremental, per-player coalescing), including its
  post-stream engine analysis — so signing in with Lichess feeds the
  player's game history into Cereveon's analysis with no manual Import
  tap.  (The auto-import passes `rated=false`, i.e. no rated filter, so
  casual games come through too; calibration reads perf ratings from the
  profile, not individual games, so casual games don't skew it, and the
  import never runs SkillUpdater.)  Failure here never fails the sign-in;
  `GET /lichess/status.active_import_job_id` (§29) exposes the job to
  clients that want progress.  Accounts that linked *before* this shipped
  are covered by the complementary cold-start backfill on `GET /auth/me`
  (§10) — no re-sign-in needed.

### Errors

- `401` — Lichess rejected the grant (invalid / expired / replayed code, or
  PKCE verifier mismatch).  The client must restart the authorization flow.
- `422` — request-shape validation (malformed `code` / `code_verifier`).
- `502` — Lichess upstream error or malformed upstream response.
- `503` — Lichess rate-limited the exchange; retry later.
- `429` — our rate limit (10 / min per client IP).

---

## 17. `POST /auth/logout`

**Host:** `llm/seca/auth/router.py`
**Auth:** `Authorization: Bearer <token>` required (raw header parse — `Header(None)` rather than `get_current_player` so a missing token surfaces as 401, not Pydantic 422; see `AUTH_HDR_02` in `test_auth_missing_header.py`).

### Request body

None (token is sufficient).

### Response

```json
{ "status": "logged_out" }
```

### Errors

- `401` — missing or malformed Authorization header, or invalid / expired token.

---

## 18. `POST /curriculum/next`

**Host:** `llm/seca/curriculum/router.py`
**Auth:** `Authorization: Bearer <token>` required

Returns the next curriculum task driven by (a) game-history-derived dominant mistake category and (b) skill-vector fallback. Backs the Android post-game training prompt.

### Request body

**None.** The route signature is `(player=Depends(get_current_player), db=Depends(get_db))` — no body parameter. The player identity is derived from the bearer token. Pre-PR-27 (2026-05-15) the Android client sent `{"player_id": "<id>"}` here which the server silently dropped; the wire-noise was removed when this section's contract was re-pinned.

### Response

```json
{
  "topic":             <string>,         // e.g. "tactics", "endgame"
  "difficulty":        <"easy" | "medium" | "hard">,
  "exercise_type":     <string>,         // see note — "puzzle"|"opening_line"|"middlegame_plan"|"endgame_drill"|"mixed_training"
  "payload":           <object>,         // type-specific fields
  "recommendations":   [ {"category": <string>, "priority": <int>, "rationale": <string>}, ... ],
  "dominant_category": <string | null>   // from HistoricalAnalysisPipeline
}
```

`difficulty` is the band string emitted by
`CurriculumPolicy.choose_difficulty()` — three discrete tiers, not a
continuous 0..1 fraction.  Earlier revisions of this section advertised
`<float 0..1>` to align with a draft contract; the implementation has
always shipped the band string and the doc was the outlier.  The pin
was caught and corrected 2026-05-25 — the Android client
(`CurriculumRecommendation.difficulty`) is `String` from that release
on, and the deserialiser would otherwise throw
`JsonDecodingException` at every call site.  Bidirectional regression
guards: `llm/tests/test_curriculum_next_contract.py::CURR_DIFFICULTY_VALID_LEVEL`
(server side) + `INT_CURR_DIFFICULTY_PARSED` and `INT_CURR_PROD_SHAPE`
in `GameApiClientCurriculumTest` (client side).

`exercise_type` is the value emitted by
`CurriculumPolicy.choose_exercise_type()`, keyed off `topic`: `puzzle`
(tactics), `opening_line` (`opening` / `opening_principles`), `middlegame_plan`
(`middlegame`), `endgame_drill` (`endgame`), and `mixed_training` — the
defensive default for an unrecognised topic.  Earlier revisions of this
section advertised `"puzzle" | "drill" | "game" | "explanation"`; that was the
vocabulary of the retired `task_selector` module (the orphaned `/next-training`
cluster), never what `/curriculum/next` shipped.  Corrected 2026-06-04
alongside closing the mapping gap where the skill-vector fallback topics
`middlegame` and `opening_principles` degraded to `mixed_training` (and a dead
`time_management` → `blitz_simulation` entry was removed).  The Android client
treats `exercise_type` as an opaque display string
(`MainActivity.formatCurriculumChip` uppercases it), so the expanded value set
needs no coordinated client release.  Regression guard:
`llm/tests/test_curriculum_next_contract.py::TestCurriculumExerciseTypeMapping`.

### History — `GET /next-training/{player_id}` retired in PR 26

§2 used to be a parallel `GET /next-training/{player_id}` route with a different
response shape.  Both endpoints coexisted because Android called
`/curriculum/next` first and fell back to `/next-training` on failure — but
`/next-training`'s "weaknesses" were hardcoded demo data, not a real signal.
PR 26 (2026-05-15) retired the legacy route + the Android fallback path,
leaving `POST /curriculum/next` as the sole training-recommendation
contract.

---

## 19. `GET /player/progress`

**Host:** `llm/seca/analytics/router.py` (mounted on `/player`)
**Auth:** `Authorization: Bearer <token>` required

Returns the authenticated player's complete progress snapshot — backs the Progress screen and the Settings rating-display.

### Response

```json
{
  "current": {
    "rating":              <float>,
    "confidence":          <float>,
    "skill_vector":        {"<skill>": <float>, ...},
    "tier":                <"beginner" | "intermediate" | "advanced">,
    "teaching_style":      <"simple" | "intermediate" | "advanced">,
    "opponent_elo":        <int>,
    "explanation_depth":   <float>,    // 3 dp
    "concept_complexity":  <float>     // 3 dp
  },
  "history": [
    {
      "game_id":          <string>,
      "result":           <"win" | "loss" | "draw">,
      "accuracy":         <float>,
      "rating_after":     <float | null>,
      "confidence_after": <float | null>,
      "weaknesses":       <object>,
      "created_at":       <ISO-8601 string | null>
    },
    ...   // up to 20, newest first
  ],
  "analysis": {
    "dominant_category": <string | null>,
    "games_analyzed":    <int>,
    "category_scores":   {"<category>": <float>, ...},  // 4 dp
    "phase_rates":       {"<phase>":    <float>, ...},  // 4 dp
    "recommendations":   [ {"category": <string>, "priority": <int>, "rationale": <string>}, ... ]
  }
}
```

`history` is empty for a fresh player; `analysis` defaults to `{"dominant_category": null, "games_analyzed": 0, "category_scores": {}, "phase_rates": {}, "recommendations": []}` when there's no history to roll up. The `current.world_model` fields are deterministic functions of rating + confidence — see `seca/adaptation/coupling.py`.

---

## 20. `GET /seca/status`

**Host:** `server.py`
**Auth:** **none** (open endpoint, intentional)

Returns the current SECA runtime safety flag. The Android client polls this at cold-start to confirm the freeze guard is active before sending coaching requests. The endpoint runs `verify_runtime_safety(world_model)` per-request, not just the boot-time `SAFE_MODE` constant, so a lazy-loaded forbidden `brain.*` module after startup flips this to `False` on the next call.

### Response

```json
{ "safe_mode": <bool> }
```

`safe_mode` is `true` on the happy path. A `false` value indicates either (a) a forbidden brain module entered the live runtime after startup, or (b) `verify_runtime_safety` raised and the endpoint fell back to the module-level `SAFE_MODE` constant (which would be `false` on a misconfigured deployment that bypassed the lifespan guard).

Pre-PR-6 the response also carried `bandit_enabled` and `version` fields; both were removed as information-disclosure surfaces with no compensating use case. Clients MUST NOT assume any extra fields.

---

## 21. `POST /chat/stream`

**Host:** `server.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 10 / minute

Streaming variant of §5 (`POST /chat`) — same LLM pipeline, same boundary validators, same fallback-to-deterministic on exhaustion — emitted as Server-Sent Events. Real token streaming (validate-before-emit, PR #223): DeepSeek tokens are forwarded as they arrive, with the FORBID validator gates run on every partial buffer (plus a lookahead hold-back) so no forbidden content is ever emitted; REQUIRE gates run at stream end. A rejected attempt is retried server-side with the same targeted rephrase hints as `POST /chat` (retry parity, 2026-07-09) before the deterministic fallback is served. See `docs/ARCHITECTURE.md` → Streaming Output Validation.

### Request body

Same shape as `POST /chat`.

### Response

`Content-Type: text/event-stream`, terminated by exactly one `done` **or** one `abort` event:

```
data: {"type": "chunk", "text": "<validated text slice>"}\n\n
...
data: {"type": "done", "engine_signal": {...}, "mode": "CHAT_V1"}\n\n
```

```
data: {"type": "abort", "reply": "<full replacement reply>", "engine_signal": {...}, "mode": "CHAT_V1"}\n\n
```

The `done` event carries the ESV + mode; the assembled chunks are the reply. The `abort` event carries a **full replacement reply** the client must render in place of any partially-streamed text — either a retry-recovered validated LLM reply or the deterministic fallback (the client cannot and need not distinguish them; the wire shape is identical). Clients should buffer chunks and treat the terminal event as authoritative for engine state.

### Errors

- `401` — auth failure (no `X-Auth-Token` rotation header on failure paths — see §10).
- `402` — daily chat quota exhausted (entitlements; dormant unless `SECA_ENTITLEMENTS_ENFORCED` is on). Same Shape B body as §5's 402, returned as a **plain HTTP JSON response before the SSE stream opens** — never as an SSE `abort` event — and before the rotation takeover, so no `X-Auth-Token` ships and the presented JWT stays valid. The Android client already reads non-200 bodies before opening the SSE reader. The turn is consumed only at the stream's terminal event (`done`/`abort`), exactly where history is persisted, so an interrupted or failed stream never eats quota.
- `429` — rate limit.
- `500` — boundary validation rejected both the LLM reply AND the deterministic fallback (rare; the deterministic builder is constructed to pass every gate). Surfaces in the Android client as the "Coach is offline" fallback string.

### Persistence

Chat history is saved via `seca/chat/repo.save_exchange` best-effort **at the stream's terminal event** (`done`/`abort` — the reply text only exists once validation settles; real streaming, PR #223), in the same place the quota turn is recorded. A transport failure mid-stream therefore neither persists the turn nor consumes quota.

---

## 22. `GET /chat/history`

**Host:** `server.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Returns recent chat turns for the authenticated player. Backs `ChatBottomSheet.preloadServerHistory` so a conversation survives process restarts, device swaps, and reinstalls.

### Query parameters

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `limit` | `int` | `HISTORY_DEFAULT_LIMIT` (50) | Bounded to `[1, HISTORY_MAX_LIMIT=200]` server-side. |
| `game_id` | `string` | _(none)_ | Optional — scope history to one game's thread (the client's current `games.id`). Omitted → player-global history (every turn, all games). Absurd lengths (> 64) are ignored (fall back to player-global) rather than 422-ing the fetch. |

### Response

```json
{
  "turns": [
    {
      "id":         <int>,
      "role":       <"user" | "assistant">,
      "content":    <string>,
      "fen":        <string>,
      "mode":       <string>,           // e.g. "CHAT_V1"
      "created_at": <ISO-8601 string | null>
    },
    ...
  ]
}
```

Turns are returned chronologically (oldest first) so the client can `addAll` directly without re-ordering. Cross-player isolation is by `WHERE player_id = ?` in the repo layer; the route is Bearer-only so the player_id is the authenticated one. No client-supplied player filter is accepted. `game_id`, when supplied, only adds `AND game_id = ?` WITHIN the authenticated player's rows — an organizational sub-filter, not a security boundary.

---

## 23. `POST /repertoire`

**Host:** `llm/seca/repertoire/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Upsert an opening line (by `eco`). Seeds the default repertoire on first write so a fresh user editing a canonical line materialises a persistent copy.

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `eco` | `string` | `^[A-E][0-9]{2}$` (standard) OR `^[A-Z][0-9A-Z]{1,7}$` (user-coined). ≤ 8 chars, no control characters. |
| `name` | `string` | ≤ 200 chars, no control characters. |
| `line` | `string` | ≤ 500 chars, no control characters. |
| `mastery` | `float` | 0.0 ≤ value ≤ 1.0. Default `0.0`. |

### Response

Same shape as §14 (`GET /repertoire`) — full updated list so the client re-renders in one round-trip.

---

## 24. `DELETE /repertoire/{eco}`

**Host:** `llm/seca/repertoire/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Remove an opening from the player's saved list. Path `eco` is validated the same way as §23's body `eco`.

### Response

Same shape as §14 (`GET /repertoire`) — full updated list.

### Errors

- `404` — opening not found. The Android client treats this as "already gone" and refreshes the list either way.

---

## 25. `POST /repertoire/{eco}/active`

**Host:** `llm/seca/repertoire/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Mark `{eco}` as the player's active line. The "exactly one active" invariant is enforced by an atomic two-write transaction in `seca/storage/repo.set_active_opening`.

### Request body

None.

### Response

Same shape as §14 — full updated list (so the client sees the new active flag + every other line's flag flipped off).

### Errors

- `404` — `eco` doesn't exist for this player after seeding (i.e. it's neither a default nor something they've added).

---

## 26. `POST /repertoire/{eco}/drill-result`

**Host:** `llm/seca/repertoire/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Apply one drill outcome to the named opening's mastery via an exponential-moving-average update:

```
new = clamp(old + _MASTERY_EMA_STEP * (outcome - old), 0.0, 1.0)
```

`_MASTERY_EMA_STEP = 0.2` — five perfect drills move a fresh line from `0` to `~0.67`; one bad drill of a well-mastered line never collapses it below ~80% of the previous value.

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `outcome` | `float` | 0.0 ≤ value ≤ 1.0. Android maps "Nailed it" / "Mostly" / "Forgot it" to `1.0` / `0.6` / `0.2`. |

### Response

Same shape as §14 — full updated list.

### Errors

- `404` — opening not found, or row vanished mid-update (race; rare).

---

## 27. `POST /lichess/link`

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 10 / minute

Attach a Lichess account to the authenticated player, **proving
ownership via OAuth** — the same PKCE authorization-code flow as "Sign
in with Lichess" (§16a), through a dedicated redirect
(`ai.chesscoach.app://lichess-link`).  The client sends the one-time
authorization `code` + its PKCE `code_verifier`; the server performs
the token exchange, reads the verified account, and **revokes the
Lichess token immediately** (it never reaches the device).  On first
link (player still at default rating 1200 + confidence 0.5), seeds the
player's rating + confidence from the matching Lichess perf rating
(prefers `rapid`, then `blitz`, then `classical`).

This replaces the former self-asserted `{username}` body *(2026-07-17)*,
which let a logged-in player attach any handle they did not own.
Because the identity is now verified, a successful link **claims** the
handle even if another account had previously self-asserted it
(`claim_from_other_player=True`), mirroring OAuth sign-in.

The Lichess account is the only external-platform link supported in
this release.  Imported `GameEvent` rows carry `source='lichess'` and
the Lichess game ID (see §3 "Imported games" and the lazy-re-analysis
note below).

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `code` | `string` | Lichess one-time authorization code from the redirect.  Matched against `AUTH_CODE_RE`. |
| `code_verifier` | `string` | The PKCE verifier for this attempt (RFC 7636 §4.1 shape; `CODE_VERIFIER_RE`). |

The redirect URI is fixed server-side to `LICHESS_OAUTH_LINK_REDIRECT_URI`
(default `ai.chesscoach.app://lichess-link`) — clients never send it, and
it is a **distinct host** from sign-in's `ai.chesscoach.app://lichess-auth`
so the two flows never cross wires.  See §16a for the sign-in flow that
mints the `code` + `code_verifier`; linking reuses that machinery with the
dedicated link redirect.

### Response

```json
{
  "platform":          "lichess",
  "external_username": <string>,
  "linked_at":         <ISO-8601 string | null>,
  "calibration": {
    "applied":     <bool>,
    "reason":      <string | absent>,
    "perf":        <"rapid" | "blitz" | "classical" | absent>,
    "rating":      <float | absent>,
    "confidence":  <float | absent>,
    "games_basis": <int | absent>,
    "provisional": <bool | absent>
  }
}
```

- `external_username` is the **canonical lowercase** Lichess id, not
  the user-submitted casing.  Clients should display this verbatim.
- `calibration.applied = true` means the player's rating + confidence
  were updated; `false` means they were preserved (either no eligible
  perf or the player had already moved off defaults via in-app play).
- `reason` is `"player_already_calibrated"` or `"no_eligible_perf"` on
  the `applied=false` paths.

### Errors

- `400` — `code` / `code_verifier` failed schema validation (malformed
  shape).
- `401` — the Lichess authorization-code exchange failed (expired /
  replayed code, wrong verifier, or redirect mismatch).  Detail:
  `"Lichess authorization failed"`.  Restart the OAuth flow.
- `502` — Lichess upstream error (5xx, an unexpected 4xx, or an
  unparseable body) while exchanging the code or reading the account.
  Detail: `"Lichess upstream error"`.
- `503` — Lichess is busy / rate-limiting.  Detail: `"Lichess is busy;
  try again shortly"`.

Because ownership is now verified, this endpoint no longer returns `404`
(no username is looked up) or `409` (a verified link **claims** the
handle rather than rejecting it).

### Trust-boundary note

Linking requires a **verified OAuth identity** (the code exchange
above), closing the prior gap where a logged-in player could
self-assert — and thereby link — a Lichess handle they did not own.
The token is exchanged server-side and revoked immediately, so the
device never holds a Lichess credential.

This endpoint is the only place that mutates `Player.rating` /
`Player.confidence` from Lichess data, and it does so at most once per
player (calibration is gated on default values).  Imported games never
touch the rating model — that invariant is the reason backfilling years
of historical games does not whipsaw the in-app FIDE-style Elo.

---

## 28. `DELETE /lichess/link`

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 10 / minute

Detach the player's Lichess link.  Already-imported `game_events` rows
are **retained** as game history; only the `linked_accounts` row is
removed.

### Request body

Empty.

### Response

```json
{ "unlinked": <bool> }
```

- `true` — a link existed and was removed.
- `false` — the player had no Lichess link to begin with (idempotent).

---

## 29. `GET /lichess/status`

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** (not rate-limited; cheap read used for client polling.)

Report the player's current Lichess link state and import counters.

### Request

No body, no query params.

### Response — when not linked

```json
{ "linked": false }
```

### Response — when linked

```json
{
  "linked":                true,
  "platform":              "lichess",
  "external_username":     <string>,
  "linked_at":             <ISO-8601 string | null>,
  "last_imported_at":      <ISO-8601 string | null>,
  "imported_game_count":   <int>,
  "active_import_job_id":  <string (UUID) | null>,
  "disconnected":          <bool>,
  "disconnected_at":       <ISO-8601 string | null>
}
```

- `last_imported_at` is the `createdAt` of the **newest** game seen in
  the most recent successful import slice — NOT the server clock at
  import time.  The next `/lichess/import` uses this value as the
  Lichess `since` parameter, so import is incremental by construction.
- `imported_game_count` counts only `game_events` rows where
  `source='lichess'` for this player; it does NOT include in-app
  games.
- `active_import_job_id` is non-null iff a v2 import job (§31) is in
  flight (`status='queued'` or `'running'`) for this player.  Added
  alongside the v2 async path so the Android client can rejoin a
  progress view after a sheet dismiss / device restart by passing
  the value to §31a.  Old v1 clients ignore unknown fields and are
  unaffected.
- `disconnected` / `disconnected_at` *(2026-07-17, reconnect flow)* —
  `true` once an import has 404'd on the linked account (closed or
  renamed on Lichess) and no clean stream has been seen since.  While
  `true` the client should surface the Reconnect state (§2.6 of the
  communication & access spec, adapted): re-run the OAuth link flow or
  `POST /lichess/link` — either clears the state, as does the next
  clean import.  There is no stored Lichess OAuth token in this
  architecture (sign-in tokens are revoked once identity is verified),
  so public-API reachability of the linked account IS the definition
  of "connected".  Additive fields; old clients ignore them.

### Self-heal for OAuth-registered players *(2026-07-19)*

A player who signed up via "Sign in with Lichess" (§16a) is the
OAuth-verified owner of their handle, so this endpoint must never report
them as not-linked.  The first-sign-in auto-link that creates the
`linked_accounts` row is best-effort, though (it never fails the
sign-in), and accounts predating it — or users who stay signed in via
JWT refresh and never re-hit `/auth/lichess` — could be left without a
row, stranding a real Lichess account on `{ "linked": false }`.

So when `status` finds no link row for a player whose
`players.lichess_user_id` is set, it **lazily recreates** the row (keyed
on that verified id — no Lichess network call and no rating calibration,
keeping the read cheap) and reports `linked: true`.  If the handle was
self-asserted on a different account, the verified owner **claims** it
(same semantics as §16a / §27).  Best-effort: a recreation failure falls
through to `{ "linked": false }` — never a 500.  It is a one-time write
per account; subsequent reads find the row.

---

## 30. `POST /lichess/import`

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 6 / minute

Pull the next slice of games from Lichess for the linked player.
Synchronous: the request returns when the slice is complete (or when
the cap is reached).  No background-job framework yet — repeated calls
walk forward through history via the `last_imported_at` watermark on
`linked_accounts`.

### Request

No body.  Query parameters:

| Param | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_games` | `int` | `50` | `1 ≤ value ≤ 100` | Hard upper bound on games fetched in this call.  The 100 cap exists to bound request latency in the absence of a background-job framework. |
| `rated` | `bool` | `true` | — | Filter to rated games only. |

Server-side, the perf-type filter is hard-coded to
`["blitz", "rapid", "classical"]` and Lichess `evals=false` is pinned
(see "Trust-boundary note" below).

### Response

```json
{
  "inserted":          <int>,
  "skipped_duplicate": <int>,
  "skipped_invalid":   <int>,
  "last_imported_at":  <ISO-8601 string | null>
}
```

- `inserted` — newly stored `game_events` rows.
- `skipped_duplicate` — games whose `(source='lichess',
  external_game_id)` pair was already in the DB.  This will be
  non-zero on partial-retry calls and is part of the dedup contract.
- `skipped_invalid` — games dropped because their PGN was missing,
  oversize (> 100 000 chars), unparseable by `python-chess`, the
  linked user wasn't listed as a player, or the `id` field was
  missing.  Surfaced for observability — these are **not** errors.
- `last_imported_at` is the new value of the watermark after this
  call.  The watermark is only advanced after a clean iteration; a
  mid-stream failure leaves it unchanged so a retry re-scans the same
  window (dedup handles the repeated rows).

### Errors

- `400` — player has no Lichess link (`/lichess/link` first).
- `422` — `max_games` out of range or `rated` not bool.
- `502` — Lichess upstream error (5xx, malformed NDJSON, connection
  failure mid-stream).  Any rows committed before the failure are
  retained.
- `503` — Lichess rate-limited the request; `Retry-After` propagated
  when present.

### Trust-boundary note

Lichess can return its own Stockfish evaluations when the games
endpoint is called with `evals=true`.  Per `docs/ARCHITECTURE.md`,
**only the local engine pool produces trusted engine output**; this
client therefore pins `evals=false` and the import service never
populates `GameEvent.accuracy` / `GameEvent.weaknesses_json` from
Lichess data.  ESV-based coaching for an imported game is produced
lazily by re-analysing the stored PGN with the local Stockfish pool
when (and only when) the user opens that game for review.

### Versioning note

The v1 synchronous contract documented above is preserved for backward
compatibility with already-shipped Android builds that send
`X-API-Version: 1` (or no header — also routed to v1).  New clients
should target **§31 (`POST /lichess/import` v2 async)** — same path,
same query parameters, but the server returns `202 Accepted` with a
`LichessImportJob` payload and the actual Lichess stream runs on a
server-side worker thread.  The v2 path enables a determinate progress
bar on the client and is the only path that survives a sheet dismiss
or device restart cleanly (the job row carries the resumable state).
Server's `API_VERSIONS_SUPPORTED` is `("1", "2")` until the next bump.

---

## 31. `POST /lichess/import` *(v2 async, `X-API-Version: 2`)*

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 6 / minute
**Header gate:** `X-API-Version: 2`

Same path as §30; the server branches on the `X-API-Version` request
header.  When the value is `"2"`, this v2 path runs: the import is
dispatched to a thread-pool worker and the route returns 202
immediately with the freshly-created (or coalesced) job row.

The actual Lichess NDJSON stream and per-game commits happen on the
worker thread; the client polls §31a to follow progress.

### Request

Identical to §30:

| Param | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_games` | `int` | `50` | `1 ≤ value ≤ 100` | Hard upper bound on games fetched in this call.  Used as the progress bar denominator client-side ("Imported X of up to Y"). |
| `rated` | `bool` | `true` | — | Filter to rated games only. |

Plus the header gate: requests without `X-API-Version: 2` fall through
to §30's v1 path.

### Response

```json
{
  "job_id":            <string (UUID)>,
  "status":            <"queued" | "running" | "succeeded" | "failed">,
  "inserted":          <int>,
  "skipped_duplicate": <int>,
  "skipped_invalid":   <int>,
  "analyzed":          <int>,
  "target_max_games":  <int>,
  "last_imported_at_ms": <int (Unix ms) | null>,
  "error_message":     <string | null>,
  "created_at":        <ISO-8601 string>,
  "updated_at":        <ISO-8601 string>
}
```

HTTP status: **202 Accepted**.

- `job_id` — server-issued UUID; pass to §31a to poll progress.
- `status` — `queued` means the worker has not picked the job up yet
  (a fresh insert, or a coalesced return while the executor is
  backlogged); any other value means an already-running job was
  coalesced into this response.  Worker dispatch happens exactly once
  server-side regardless (`start_import_job`'s dispatch callback fires
  only for freshly-created rows, inside the per-player lock).
- `inserted` / `skipped_*` — counters at the moment the row was read
  (`queued` rows are always 0; coalesced rows reflect the worker's
  progress).
- `target_max_games` — pinned at row creation; the client's progress
  bar denominator.  Subsequent coalesced calls do NOT update this
  value (so a second call with a different `max_games` returns the
  ORIGINAL target).
- `last_imported_at_ms` — Unix milliseconds of the newest game seen
  during this run.  Promoted to the canonical
  `LinkedAccount.last_imported_at` (ISO-8601) only on clean
  `succeeded`.
- `analyzed` *(2026-07-03)* — games scored by the post-stream engine
  analysis pass (see the trust-boundary note below).  Advances while
  `status` is still `running`, AFTER `inserted` reaches its final
  value — a client that only tracks `inserted` sees an unchanged bar
  for the analysis tail, which the deployed Android client tolerates
  (any non-terminal status keeps its polling loop alive; unknown JSON
  keys are ignored).

### Coalescing

Concurrent calls for the same player return the same `job_id`.  The
per-player lock in `llm.seca.lichess.get_player_import_lock` is the
primary guard; on Postgres the partial unique index
`ix_lichess_import_jobs_one_active_per_player` is the defense in depth.

### Errors

- `400` — player has no Lichess link (`/lichess/link` first).
- `422` — `max_games` out of range or `rated` not bool.
- `502` — service-layer crash before the worker dispatch.  (Worker
  failures DURING the stream are recorded on the job row's
  `error_message` / `status='failed'`, NOT surfaced through the POST.)

### Trust-boundary note

Same as §30: Lichess `evals=false` is pinned; the import service
never populates `GameEvent.accuracy` / `weaknesses_json` from Lichess
data.

### Post-import engine analysis *(2026-07-03)*

After the stream completes (and before the job goes terminal), the v2
worker runs `llm.seca.lichess.analysis_service.analyze_unscored_games`:
the most recently imported `source='lichess'` rows still carrying the
unscored marker (`accuracy` NULL **or** `0.0` — the ORM's Python-side
column default fires even for the stream's explicit `None`, so both
forms occur; scored rows can never collide because the ACPL mapping is
strictly positive) — including backlog from imports that predate this
feature — are re-analysed with the **local** engine pool via
`compute_accuracy_from_pgn` (the same engine-truth recompute
`/game/finish` uses, same 200 ms/ply budget), writing `accuracy` +
phase-keyed `weaknesses_json` onto each row.  From that point the
historical-analysis surfaces (curriculum, weakness charts, progress
dashboard) consume imported games exactly like in-app ones.  Bounded at
`LICHESS_ANALYSIS_MAX_GAMES` (default 20) per job — see
`docs/THREAT_MODEL.md` §T3 for the engine-minutes rationale; excess
backlog is picked up by subsequent jobs.  Pool saturation defers the
remainder without failing the job; player rating / confidence are never
mutated by this pass.

---

## 31a. `GET /lichess/import/job/{job_id}` *(v2 async)*

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 120 / minute (60/min headroom over a 2s steady-state poll)

Poll the state of a v2 import job.  Owner-scoped: returns 404 when
the job does not exist OR when it belongs to another player (we do
not differentiate, to avoid leaking the existence of other players'
jobs).

### Request

Path parameter: `job_id` (UUID returned by §31).  No body, no query.

### Response

Same shape as §31's body (200 OK).  Field semantics identical:

```json
{
  "job_id":            <string>,
  "status":            <"queued" | "running" | "succeeded" | "failed">,
  "inserted":          <int>,
  "skipped_duplicate": <int>,
  "skipped_invalid":   <int>,
  "analyzed":          <int>,
  "target_max_games":  <int>,
  "last_imported_at_ms": <int | null>,
  "error_message":     <string | null>,
  "created_at":        <ISO-8601 string>,
  "updated_at":        <ISO-8601 string>
}
```

Polling cadence: 2s is the Android client's default and the basis for
the `120/minute` rate limit (steady-state ~30/min + retry headroom).
Stop polling once `status` is `succeeded` or `failed` (terminal); the
field set is otherwise stable so a poll that observes terminal counts
can render the final summary directly.

### Errors

- `404` — job not found OR not owned by current player.

### Cancellation

There is no explicit cancel endpoint.  The two cancellation paths are:

- `DELETE /lichess/link` (§28) — cancels any active jobs for the
  player with `error_message: "link removed during import"` before
  removing the link row.  The worker observes the status change via
  its per-game refresh and exits without advancing the watermark.
- Server restart — the startup janitor
  (`cleanup_stale_import_jobs_on_startup`) sweeps any non-terminal
  rows to `failed` with `error_message: "abandoned by server
  restart"`.

---

## 32. `POST /training/solve`

**Host:** `llm/seca/training/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

Credits one verified-solve event to the authenticated player and
bumps `Player.training_xp` by `XP_PER_SOLVE` (10 at Phase 2).  The
endpoint trusts the caller's claim that a solve actually happened —
engine verification is the *caller*'s responsibility (Phase 3 will
run a move-vs-engine-best check on the client + a server-side
double-check before posting here).

### Request body

| Field         | Type             | Required | Description |
|---------------|------------------|----------|-------------|
| `source_type` | `string`         | yes      | One of `"mistake_replay"`, `"weekly_microtask"`, `"standard_puzzle"`. |
| `source_ref`  | `string \| null` | no       | Stable identifier for the solved item (e.g. `"game_<id>:move_<n>"`, a puzzle id, a digest row id).  Bounded at 200 chars; empty / whitespace-only strings are normalised to `null`. |

### Response (200)

```json
{
  "xp_awarded":   <int>,
  "training_xp":  <int>,
  "completed_at": "<ISO-8601>"
}
```

* `xp_awarded` — XP credited for THIS request.  Equals `XP_PER_SOLVE`
  for a new completion; equals `0` when the call deduped against an
  existing row (see *Idempotency* below).  Lets the client tell the
  difference between "you earned XP" and "we already had this one"
  without rendering two response shapes.
* `training_xp` — new running total on the player row.  Lets the
  client update its `PREF_TRAINING_XP` cache + Home Level/XP kicker
  without a separate `/auth/me` round trip.
* `completed_at` — ISO-8601 timestamp.  For a brand-new completion
  this is the row that was just inserted; for a dedup hit this is the
  *historical* row's timestamp.

### Idempotency

`(player_id, source_type, source_ref)` is unique at the database
level when `source_ref` is non-null: a retry of the same logical solve
returns the original row's `completed_at` with `xp_awarded=0` so the
counter doesn't double-bump.  Rows with `source_ref=null` are NOT
deduped (Postgres `NULL`-distinct semantics in the unique index;
intent: catch-all completions where the caller doesn't yet have a
stable identifier).  Phase-3 callers should always supply a stable
ref when dedup matters.

The endpoint handles the unique-constraint race window (two
concurrent requests both pass the pre-check, one commits first, the
other hits the index) by rolling back, re-fetching the existing row,
and returning the dedup response — same observable behaviour as the
pre-check path.

### Rate limit

`60/minute` per client (shared slowapi limiter).  Tuned to permit
normal solve-burst patterns (a weekly digest's 3 micro-tasks finished
back-to-back) without permitting scripted XP farming.

### Errors

| Status | Cause |
|--------|-------|
| `400`  | `source_type` not in the allowed set; `source_ref` exceeds 200 chars. |
| `401`  | Missing or invalid `Authorization` header. |
| `429`  | Rate limit exceeded. |
| `500`  | Unique-constraint race that left no committed row (should be unreachable; logged as `IntegrityError on /training/solve but no row found`). |

---

## 33. `POST /training/verify-replay`

**Host:** `llm/seca/mistakes/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

Verify a single mistake-replay attempt against the engine.  Trust
anchor for the Phase 3 XP-credit path: the Android replay sheet
calls this BEFORE calling `POST /training/solve`, so an unverified
move never moves the counter.

### Request body

| Field      | Type     | Required | Description |
|------------|----------|----------|-------------|
| `fen`      | `string` | yes      | Position the player was looking at when they erred. Non-empty, ≤ 200 chars.  Typically the value of `biggest_mistake.fen` from a recent `/game/finish` response. |
| `move_uci` | `string` | yes      | Move the player is proposing as a fix, in UCI notation (e.g. `e2e4`, `e7e8q`). Non-empty, ≤ 8 chars. |

### Response (200)

```json
{
  "is_correct":      <bool>,
  "engine_best_uci": <string>,
  "eval_loss_cp":    <int>
}
```

* `is_correct` — `true` when the user's move gives up at most
  `VERIFY_THRESHOLD_CP` centipawns (30) vs the engine's best move
  in that position.  `false` means "engine ran and says no" —
  the replay UI shows "Not quite, try again" and the user retries.
* `engine_best_uci` — the move Stockfish prefers.  Surfaced even on
  a correct attempt so the UI can offer a "Here's what the engine
  plays" peek without a second round-trip.  Empty string when the
  engine returned no move (edge case; should not happen on a legal
  position).
* `eval_loss_cp` — signed centipawn delta from the player's POV.
  Positive when the user's move is worse than the engine's; can be
  slightly negative on engine search noise (still counts as
  `is_correct=true` since the threshold check is one-sided).

### Rate limit

`60/minute` per client (shared slowapi limiter).  Tuned to permit
normal puzzle-burst patterns while preventing scripted oracle
queries against the engine.

### Errors

| Status | Cause |
|--------|-------|
| `400`  | `fen` cannot be parsed; `move_uci` not legal in the given position; either string exceeds its length cap. |
| `401`  | Missing or invalid `Authorization` header. |
| `429`  | Rate limit exceeded. |
| `503`  | Engine pool unavailable (boot-time failure or queue timeout). Client can show a soft retry. |

---

## 34. `GET /coach/plan/today`

**Host:** `llm/seca/coach/study_plan/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

Return the player's most recent active per-mistake study plan + the
puzzle currently due, or `null` when no active plan exists.

A study plan is generated as a side-effect of `POST /game/finish` when
that endpoint identified a `biggest_mistake` (§3) — a background task
on the server writes a 3-puzzle **spaced-repetition schedule** keyed to
the one originating mistake.  Day 0 is the exact mistake position; days
3 and 7 are theme-matched library variants.  Pacing is sequential AND
calendar-gated: a day unlocks only once every earlier day is solved
**and** its `due_at` (plan creation + 0 / 3 / 7 days) has elapsed, so
day 3 opens no earlier than 3 days after the plan was created, day 7 no
earlier than 7, and the plan cannot be cleared in one sitting.  At most
one day is due at a time.  (Pacing was briefly purely sequential —
solve-to-unlock, all `due_at` at creation; plans from that window keep
advancing solve-to-unlock because their `due_at` values are already in
the past.)

Phase 3 (live): the endpoint serves the persisted plan with
LLM-generated `theme` + `verdict` (phase 2) AND with day-3 / day-7
puzzles replaced by theme-matched library variants from the curated
YAML corpus.  Day-0 is always the player's original mistake position.
The day-3 / day-7 variants lead with the day-0 mistake's OWN `theme`
(a king-safety mistake yields king-safety practice); the aggregate
`anchor_category` is only a backfill pool, consulted when that specific
theme is too thin to fill both days.

Practice selection is **role-aware** (2026-07-15, launch feedback):
Lichess theme puzzles are always solved from the winning side, so themes
whose lesson is protecting the player's OWN material — `queen_safety`,
`hung_piece`, `king_safety`, `back_rank` — source from Lichess's
`defensiveMove` angle (find the move that keeps your own position safe)
instead of the attacker-seat theme slugs, and the corpus entries for
those themes drill saving your own queen/piece/rank.  Exploit themes
(`fork`, `pin` — the mistake was MISSING the tactic) keep their own
slugs.  Variants also prefer **multi-move** puzzles (at least two solver
decisions; see `today_puzzle.solution_line_uci`) and the day-3 / day-7
pair is ordered by puzzle rating ascending so the week ramps up.

The `theme` field is one of the following tags:

```
king_safety
fork
pin
back_rank
hung_piece
queen_safety
tempo
opening_principles
endgame_technique
generic
```

`"generic"` is both an explicit theme (used by the LLM when none of
the named themes fit) AND the fallback value when the LLM path failed
— clients should not treat the two cases differently.  An empty
`verdict` is the only signal that the LLM did not produce a usable
output.

### Request

No body.  Reads the authenticated player from the bearer token.

### Response (200, plan exists)

```json
{
  "plan_id":         <string>,
  "theme":           <string>,
  "verdict":         <string>,
  "anchor_category": <string | null>,
  "status":          <string>,
  "total_days":      3,
  "today_puzzle": {
    "day_offset":         <int>,
    "fen":                <string>,
    "expected_move_uci":  <string>,
    "solution_line_uci":  [<string>, ...],
    "source_type":        <string>,
    "due_at":             <string>
  } | null,
  "days": [
    {
      "day_offset":  <int>,
      "due_at":      <string>,
      "completed":   <bool>,
      "is_due":      <bool>,
      "source_type": <string>
    }
  ]
}
```

| Field | Type | Notes |
|-------|------|-------|
| `plan_id` | `string` | UUID of the `mistake_study_plans` row.  Stable for the life of the plan.  Pass it (with a `day_offset`) to `POST /coach/plan/puzzle/complete` (§35) when a day is solved. |
| `theme` | `string` | Theme tag of the mistake, from a fixed vocabulary (see below).  Populated by a single-shot LLM call run in the background after `/game/finish`.  Falls back to `"generic"` when the LLM was unreachable, returned out-of-vocabulary, or its output failed the Mode-2 validators on both retries. |
| `verdict` | `string` | LLM-written ≤ 60-word retrospective on the originating mistake.  Mode-2-validator-clean: no specific moves (no algebraic notation, no UCI), no engine mentions, no advisory phrasing.  Empty string when the LLM path failed unrecoverably; Android `TodaysDrillCard` hides the coach-note line in that case. |
| `anchor_category` | `string \| null` | The player's aggregate dominant weakness — one of `opening_preparation` / `tactical_vision` / `positional_play` / `endgame_technique` (from `HistoricalAnalysisPipeline` over recent games at `/game/finish`).  The overview renders it as the week's focus ("This week: Tactics").  For puzzle SELECTION it is only the backfill: day-3 / day-7 lead with the day-0 mistake's own `theme` and fall back to this category's theme set when that theme is too thin.  `null` for legacy plans and players with too little history to surface a dominant category (the backfill then degrades to the generic bucket).  Distinct from `theme`, which describes — and now drives the practice for — the day-0 mistake's own motif. |
| `status` | `string` | Plan lifecycle: `"active"` while the week is in progress, `"completed"` once every day is solved.  `GET` only ever returns `"active"` plans (a completed plan returns `null`); `POST /coach/plan/puzzle/complete` (§35) returns the freshly-`"completed"` plan so the client can show the week-complete state. |
| `total_days` | `int` | Number of puzzles in the plan.  Always `3` in phase 1; surfaced as a field so the UI can render "Day N of M" without hard-coding. |
| `today_puzzle` | `object \| null` | The day available to solve right now: the FIRST incomplete day, once its `due_at` has elapsed.  `null` when the next day is still calendar-locked (e.g. day-0 solved on creation day; day-3 opens at +3 days — the client hides the drill card) or when every day is solved (the plan is about to flip to `completed`). |
| `today_puzzle.day_offset` | `int` | One of `0`, `3`, `7`.  Maps to "Day 1 / 3", "Day 2 / 3", "Day 3 / 3" via a static client-side label. |
| `today_puzzle.fen` | `string` | Position the puzzle drops the user into. |
| `today_puzzle.expected_move_uci` | `string` | The expected move at that FEN (UCI), a display / short-circuit HINT only — it is never the correctness oracle.  Whether a replay counts as solved is decided by the LOCAL engine on `POST /training/verify-replay` (§ mistakes).  For day-0 puzzles this is the player's ORIGINAL bad move — the puzzle asks the user to find a stronger alternative.  For library variants it is the puzzle's expected solution (Lichess's first solution move for a Lichess-sourced puzzle), always equal to `solution_line_uci[0]` when that list is non-empty. |
| `today_puzzle.solution_line_uci` | `string[]` | Full solution walk for `"library"` puzzles, in UCI: SOLVER moves at even indices, opponent replies at odd ones, always ending on a solver move.  A single-decision puzzle carries just its one move.  The client walks the line — the user must find each solver move (each judged by the LOCAL engine via `POST /training/verify-replay` at the then-current position; an engine-approved deviation from the line also counts as solved), and the client auto-plays the odd-index opponent replies.  Same hint-only trust posture as `expected_move_uci`: the line never decides correctness by itself.  **Empty (`[]`) for `"original"` day-0 puzzles** — their `expected_move_uci` is the player's bad move, not a solution — and clients must then fall back to the single-move drill flow.  Added 2026-07-15; older clients ignore the field (single-move behaviour unchanged). |
| `today_puzzle.source_type` | `string` | `"original"` for day-0 (the player's actual mistake) and any day whose library lookup didn't find a match; `"library"` for day-3 / day-7 practice puzzles.  Library puzzles are matched to the day-0 mistake's theme, side-to-move AND role (see the role-aware selection note above), sourced live from Lichess (`GET /api/puzzle/next`) when available and falling back to the curated YAML corpus (`llm/seca/coach/study_plan/library/`) otherwise — wire-identical either way (both are `"library"`).  Lets the UI title the puzzle accordingly ("Replay your mistake" vs "Practice: <theme>"). |
| `today_puzzle.due_at` | `string` | ISO-8601 UTC timestamp of when this day unlocked (plan creation + `day_offset` days).  Always `<= now` when `today_puzzle` is non-null — the endpoint only serves puzzles that are actually due. |
| `days` | `array` | The full week schedule, ordered by `day_offset` (always `total_days` entries).  Powers the week-overview screen — each entry is `completed` (done), `is_due` (the one to do now), or neither (a later day, locked until the earlier days are solved). |
| `days[].day_offset` | `int` | One of `0`, `3`, `7`. |
| `days[].due_at` | `string` | ISO-8601 UTC timestamp of the earliest moment this day can unlock (plan creation + `day_offset` days).  Clients may render it as "unlocks <date>" for locked days. |
| `days[].completed` | `bool` | `true` once the day's puzzle has been solved. |
| `days[].is_due` | `bool` | `true` for the day to do now: the FIRST incomplete day, once its `due_at` has elapsed.  At most one day is `is_due` at a time and it equals `today_puzzle`; a day that is neither `completed` nor `is_due` is locked (waiting on its `due_at`, an earlier unsolved day, or both). |
| `days[].source_type` | `string` | `"original"` or `"library"`, same meaning as `today_puzzle.source_type`. |

### Response (200, no active plan)

```json
null
```

The endpoint returns HTTP 200 with a JSON `null` body when the player
has no active study plan — either no qualifying game has landed yet,
or every plan is completed.  The Android client hides the
`TodaysDrillCard` in this case.

### Puzzle completion + XP credit

On a solved day the client runs three calls, in order:

1. `POST /training/verify-replay` (§33) — engine-truth gate on the move.
2. `POST /training/solve` (§32) — credits XP, with
   `source_type = "mistake_replay"` and
   `source_ref = "plan_<plan_id>:day_<day_offset>"` so the
   `(player, source_type, source_ref)` dedup triple keeps each
   individual puzzle credit-once.
3. `POST /coach/plan/puzzle/complete` (§35) — advances the study plan
   (marks the day done; flips the plan to `completed` when all days
   are solved).

Steps 2 and 3 are distinct on purpose: XP is a global counter, plan
progress is per-plan schedule state.  Step 1 is the trust anchor;
steps 2-3 record the personal, idempotent outcome.

### Rate limit

`60/minute` per client (shared slowapi limiter).  Loose because the
endpoint is a single point read; the client is expected to poll on
each home-screen open.

### Errors

| Status | Cause |
|--------|-------|
| `401`  | Missing or invalid `Authorization` header. |
| `429`  | Rate limit exceeded. |

No 4xx beyond auth — a missing plan returns 200 with `null` body, not
404, because the absence is a normal product state.

---

## 35. `POST /coach/plan/puzzle/complete`

**Host:** `llm/seca/coach/study_plan/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

Mark one day's puzzle in a study plan as solved and advance the plan.
This closes the loop the phase-1 scaffold left open: nothing previously
wrote `MistakeStudyPuzzle.completed_at`, so `GET /coach/plan/today` (§34)
re-served day 0 forever and plans never reached `completed`.  The client
calls this as step 3 of the completion flow (see §34 → "Puzzle
completion + XP credit").

### Trust posture

Records plan PROGRESS, not engine truth.  The engine-truth gate already
happened on `POST /training/verify-replay` (§33), which the client runs
first.  Plan progress carries no cross-user value, so — like
`POST /training/solve` (§32) — the endpoint trusts the caller's
assertion.  It is **idempotent** (re-completing a day is a no-op that
returns 200) and **ownership-scoped** (a plan not owned by the
authenticated player is indistinguishable from a missing one → 404).

### Request

```json
{
  "plan_id":    <string>,
  "day_offset": <int>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `plan_id` | `string` | UUID from the `plan_id` field of `GET /coach/plan/today` (§34). |
| `day_offset` | `int` | The day being completed — one of `0`, `3`, `7` (from `today_puzzle.day_offset`). |

### Response (200)

The full plan, in the **same shape as `GET /coach/plan/today` (§34)**
(`plan_id`, `theme`, `verdict`, `anchor_category`, `status`,
`total_days`, `today_puzzle`, `days`).  Unlike `GET`, this returns the
plan even when the completion
flipped `status` to `"completed"`, so the client can render the next due
puzzle — or the week-complete state — without a second round-trip.
After completing a day whose successor is still calendar-locked (the
common case: day-0 solved on creation day, day-3 unlocks at +3 days)
the returned plan carries `today_puzzle: null`; the drill card hides
until the next day's `due_at` elapses.

The endpoint does not re-check `due_at` on the day being completed: it
records the caller's assertion about a solve that already happened
(same trust posture as `POST /training/solve` — plan progress has no
cross-user value, and XP is credited elsewhere).  Availability is
enforced where play starts: clients can only launch the puzzle served
in `today_puzzle` (§34), which the sequential + calendar gate controls.

### Rate limit

`60/minute` per client (shared slowapi limiter).

### Errors

| Status | Cause |
|--------|-------|
| `401`  | Missing or invalid `Authorization` header. |
| `404`  | No plan with that `plan_id` owned by the player, or the plan has no puzzle at `day_offset`. |
| `422`  | Body validation failed (missing `plan_id` / `day_offset`, wrong types). |
| `429`  | Rate limit exceeded. |

---

## 36. `POST /billing/google/verify` *(SUPERSEDED by §40 RevenueCat webhook)*

> **Superseded.** With RevenueCat as the subscription source of truth
> (§40), the app purchases through the RevenueCat SDK and the server
> learns of entitlement via the webhook — not by verifying raw purchase
> tokens here. This endpoint is retained for back-compat / non-RevenueCat
> clients; with RevenueCat the `GOOGLE_PLAY_*` credentials live in the
> RevenueCat dashboard, so this endpoint's env is unset and it answers
> `503`.

**Host:** `llm/seca/billing/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 10 / minute

Verify a Google Play purchase token server-side and activate the
purchased plan for the **authenticated player** (owner-scoped by
construction — the body carries no player identity). The client's
claim is never trusted: entitlement comes only from the Google Play
Developer API (`purchases.subscriptionsv2.get`), reached with
service-account credentials from env (`GOOGLE_PLAY_PACKAGE_NAME` /
`GOOGLE_PLAY_SA_EMAIL` / `GOOGLE_PLAY_SA_PRIVATE_KEY` — see
`.env.example`).

### Request body

```json
{
  "purchase_token": <string>,
  "product_id":     <string>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `purchase_token` | `string` | The token from Play Billing's `Purchase.getPurchaseToken()`. Non-empty, ≤600 chars, no control characters. |
| `product_id` | `string` | Play product id. Non-empty, ≤64 chars. Must be a product the server knows (`pro_monthly` → plan `pro`, `pro_yearly` → plan `pro` — both grant the same plan; the billing period is Play-side pricing, not an entitlement distinction); unknown ids → `400` **before** any Google call. `pro_monthly` matches the `upgrade.product` hint in the chat 402 body (§5). |

### Response (200)

```json
{
  "plan":       "pro",
  "product_id": "pro_monthly",
  "state":      "SUBSCRIPTION_STATE_ACTIVE"
}
```

Returned **only after** the plan flip is committed — `set_plan`
re-raises on persistence failure, so a 200 is never a fake success.
`state` is Google's `subscriptionState` verbatim.
`SUBSCRIPTION_STATE_ACTIVE`, `_IN_GRACE_PERIOD`, and `_CANCELED`
(auto-renew off, paid period still running) all count as entitled;
`_EXPIRED` does not. Expiry-driven automatic downgrade is a deferred
follow-up (Play RTDN webhook).

### Errors

| Status | Cause |
|--------|-------|
| `400`  | Unknown `product_id` (Shape A: `{"detail": "unknown product_id"}`). |
| `401`  | Missing or invalid `Authorization` header. |
| `402`  | Google answered, and the token carries no entitlement — expired / refunded / revoked / never real (Shape A: `{"detail": "purchase not active (<state>)"}`). The plan is **not** flipped. |
| `422`  | Body validation failed. |
| `429`  | Rate limit exceeded. |
| `502`  | Google OAuth / Play API unreachable or answered abnormally — no verdict exists; the client should retry later. Plan unchanged. |
| `503`  | Server has no `GOOGLE_PLAY_*` credentials configured (deploys without billing stay safe and loud, never fake-success). |

---

## 37. `GET /puzzles/next`

**Host:** `llm/seca/puzzles/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Serve one practice puzzle for the standalone puzzle trainer (the
Android Puzzles tab).  Primary source is Lichess's public puzzle
database (`GET /api/puzzle/next`, `angle=mix`) at a difficulty band
derived from the authenticated player's rating
(`skill_hint_for_rating`: `<1200` → `easier`, `1200–1800` → `normal`,
`>1800` → `harder`).  **Every** Lichess failure mode (rate limit,
upstream error, malformed body, illegal derived position, kill-switch
`PUZZLES_LICHESS_ENABLED=0`) falls back to a random skill-banded pick
from the curated study-plan corpus, so the endpoint keeps serving with
zero Lichess availability.

Takes no parameters — the server derives everything from the
authenticated player (no caller-controlled input reaches the upstream
URL; the angle and difficulty values are pinned against the Lichess
client's allowlists by `llm/tests/test_puzzles_next.py`).

### Response (200)

```json
{
  "puzzle_id":         <string>,
  "fen":               <string>,
  "expected_move_uci": <string>,
  "solution_line_uci": [<string>, ...],
  "theme":             <string>,
  "difficulty":        <string>,
  "source":            <string>,
  "rating":            <int | null>
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `puzzle_id` | `string` | Stable identifier — pass back to `POST /training/solve` (§32) as `source_ref` so each puzzle is credit-once.  Lichess picks are namespaced `lichess_<id>`; corpus picks use the YAML id (never colliding by construction). |
| `fen` | `string` | The solver's position (side to move = the side the user plays). |
| `expected_move_uci` | `string` | The source's solution move.  Display / short-circuit hint ONLY — solving is judged by the LOCAL engine via `POST /training/verify-replay` (§33), never by this field. |
| `solution_line_uci` | `string[]` | Full solution walk in UCI: SOLVER moves at even indices, opponent replies at odd ones, ending on a solver move — the trainer walks multi-move puzzles step by step (each solver move judged by the LOCAL engine via `POST /training/verify-replay` at the then-current position; the odd-index replies auto-play; an engine-approved deviation from the line counts as solved).  Single-decision puzzles carry just their one move — the list is never empty, and `solution_line_uci[0]` always equals `expected_move_uci`.  Same hint-only trust posture.  Added 2026-07-16; older clients ignore the field (single-move behaviour unchanged). |
| `theme` | `string` | Corpus theme tag (`THEME_VOCABULARY`) for library picks; `"mix"` for Lichess picks (the trainer serves un-themed practice). |
| `difficulty` | `string` | `beginner` / `intermediate` / `advanced` — the corpus band for library picks, derived from the Lichess puzzle rating otherwise. |
| `source` | `string` | `"lichess"` (live fetch) or `"library"` (curated corpus fallback). |
| `rating` | `int \| null` | Lichess puzzle rating when known; `null` for corpus picks or unrated Lichess puzzles. |

### Solve + XP loop (client-side)

Identical trust anchor to every other training source:

1. `POST /training/verify-replay` (§33) — the local engine judges the
   attempt.
2. `POST /training/solve` (§32) — credits XP with
   `source_type = "standard_puzzle"` and `source_ref = <puzzle_id>`,
   deduped by the `(player, source_type, source_ref)` unique triple.

Lichess evaluations are never requested or propagated
(`evals` is not part of `/api/puzzle/next`; see the trust-boundary
notes in `llm/seca/lichess/client.py`).

### Errors

| Status | Cause |
|--------|-------|
| `401`  | Missing or invalid `Authorization` header. |
| `429`  | Rate limit exceeded (Shape B). |
| `503`  | Lichess unavailable/disabled AND the local corpus is empty (misbuilt image).  The client shows a soft retry message. |

---

## 38. `POST /feedback`

**Host:** `llm/seca/feedback/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 5 / minute

Persist one free-form product-feedback message from the authenticated
player (the game drawer's "Send feedback" form on Android).  Rows land
in the `feedback_messages` table for the operator to read; they are
**never** read back into any coaching, prompt, retrieval, or adaptation
path, and the message body is never logged (the receipt log line
carries only the server-issued player id + message length).

### Request body

| Field         | Type             | Required | Description |
|---------------|------------------|----------|-------------|
| `message`     | `string`         | yes      | Free-form feedback text.  Trimmed; must be non-blank after trim; at most 2000 chars. |
| `app_version` | `string \| null` | no       | Client-reported app version (`BuildConfig.VERSION_NAME` on Android).  Trimmed; blank normalised to `null`; at most 64 chars. |

### Response (200)

```json
{
  "status": "received",
  "id":     "<uuid>"
}
```

* `status` — fixed literal `"received"` (mirrors `/game/coach-feedback`'s
  `{"status": "recorded"}` shape).
* `id` — server-issued row id, usable as a reference in support
  conversations.

Fire-and-forget from the client's perspective: no dedup (the same text
twice is two rows — feedback is not idempotent by nature) and clients
should not block gameplay on the result.

### Errors

| Status | Cause |
|--------|-------|
| `401`  | Missing or invalid `Authorization` header. |
| `422`  | Blank message; message > 2000 chars; app_version > 64 chars. |
| `429`  | Rate limit exceeded (Shape B). |

---

## 39. `POST /game/{event_id}/review`

**Host:** `llm/seca/review/router.py`
**Auth:** `Authorization: Bearer <token>` required; owner-scoped
**Rate limit:** 6 / minute

Start (or coalesce / retry) the post-game AI review job for one
**imported Lichess** game.  The review is the job-row + thread-pool +
polling pattern of §31: the row's `status` advances through
`queued → running → engine_done → complete | failed`, and the client
polls §39a while non-terminal, rendering whatever stages have landed
(three-wave progressive UX).

Semantics by existing-row state (at the current `analysis_version`):

| Existing row | Behaviour | HTTP |
|--------------|-----------|------|
| none | create + dispatch worker | `202` |
| `queued` / `running` / `engine_done` | coalesce (no new work) | `202` |
| `failed` | requeue the same row | `202` |
| `complete`, `llm.outcome == "full"` | serve as-is | `200` |
| `complete`, `llm.outcome ∈ {"fallback", "skipped_entitlement"}` | re-run the LLM stage only ("Try coach review again"); if the entitlement cap still blocks, serve unchanged | `202` / `200` |

**Never 402s.**  The engine review (Wave 2: banded eval series, move
classification counts, critical moments) is free on every imported
game; the entitlements gate only the LLM stage — the `import_analysis`
monthly ceiling (free 3 / pro 50) plus, for pro, the
`import_analysis_daily` smoothing cap (10/day; protects the shared
Stockfish pool from bulk backfills, not the token budget —
~$0.002/review measured 2026-07-15).  A blocked stage completes the
review with `llm.outcome == "skipped_entitlement"` (plus a
`skipped_metric` field naming the bucket); the `entitlement` snapshot
reports the BINDING bucket — `metric` is `"import_analysis"` (monthly)
or `"import_analysis_daily"` (today) — which drives the client copy
(upgrade CTA for free; "more tomorrow" for a capped pro).  Admission
is per-game-subject (`entitlements.admit`) in BOTH buckets, so
re-reviewing the same game never double-charges either.

### Response body (200 / 202) — shared with §39a

```json
{
  "review_id":        "<uuid>",
  "event_id":         "<uuid>",
  "status":           "queued | running | engine_done | complete | failed",
  "analysis_version": 1,
  "review_mode":      "standard | strategic | null",
  "engine":           { … } ,
  "moments":          [ … ] ,
  "llm":              { … } ,
  "error_message":    "string | null",
  "entitlement":      { "metric": "import_analysis", "allowed": true,
                        "plan": "free", "limit": 3, "used": 1,
                        "remaining": 2 },
  "created_at":       "ISO-8601",
  "completed_at":     "ISO-8601 | null"
}
```

`engine`, `moments`, `llm` are `null` until their stage lands:

* `engine` *(Wave 2)* — `bands` (list of `losing | worse | equal |
  better | winning`, player-relative, one per board position: index 0
  = start position, index i = after ply i — same indexing as
  §7a `positions`), `accuracy` (0..1 float, same value
  `/game/history` shows), `counts` (`blunders` / `mistakes` /
  `inaccuracies` ints), `moves_analyzed`, `player_color`, `plies`,
  and `meta` (PGN-header echo: `white`, `black`, `white_elo`,
  `black_elo`, `time_control`, `opening`, `eco`, `date`,
  `termination` — keys absent when the header is absent).
  **No raw centipawns are ever emitted** — the eval series is banded
  server-side, enforcing the Atrium no-numeric-eval invariant at the
  wire.
* `moments` — up to 3 critical moments, chronological:
  `{ply, move_number, san, moment_type (blunder | missed_win |
  mistake | punished_mistake | strategic), phase, band_before,
  band_after, fen_before, fen_after, clock_remaining_s}`.
* `llm` *(Wave 3)* — `{moments: [{ply, text, source: "llm" |
  "fallback"}], verdict: {text, source}, outcome: "full" | "fallback"
  | "skipped_entitlement"}`.  `skipped_entitlement` carries no texts.
  Texts passed the Mode-2 gates (no notation, no engine mentions) or
  are the deterministic coach-voice fallbacks.

### Errors

| Status | Cause |
|--------|-------|
| `400`  | `event_id` longer than 64 chars (plain-string detail), or ineligible game — detail is then an object `{"code": "not_lichess" \| "too_short", "message": "…"}`. |
| `401`  | Missing or invalid `Authorization` header. |
| `403`  | Event belongs to another player. |
| `404`  | Event does not exist. |
| `429`  | Rate limit exceeded (Shape B). |

---

## 39a. `GET /game/{event_id}/review`

**Host:** `llm/seca/review/router.py`
**Auth:** `Authorization: Bearer <token>` required; owner-scoped
**Rate limit:** 120 / minute (2s poll cadence + retries, same budget
as §31a)

Poll the review row.  `200` with the §39 body; `404` when no review
exists at the current `analysis_version` (client shows the "Get coach
review" button).  Same `400`/`401`/`403`/`404` guards as §39.

---

## 40. `GET /notifications` + lifecycle mutations

**Host:** `llm/seca/notifications/router.py`
**Auth:** `Authorization: Bearer <token>` required; owner-scoped
**Rate limit:** GET not rate-limited (cheap read, fetched on Home
resume); each mutation 30 / minute.

In-app notification feed (communication & access spec §5, adapted to
this codebase).  Rows are created **server-side only** — the Lichess
import worker (`game_analyzed`, batched per §5.4) and the disconnect
detector (`system_alert` with the reconnect action).  There is no
create endpoint.

### `GET /notifications`

Visible rows (not dismissed, not expired, ≤ 30 days old), newest
first, capped at 50.  Read rows stay in the feed; the client renders
them under a "Read" section.

```json
{
  "notifications": [
    {
      "id":            <string (UUID)>,
      "type":          "game_analyzed" | "system_alert",
      "priority":      "low" | "medium" | "high" | "critical",
      "title":         <string>,
      "body":          <string>,
      "action":        "open_history" | "lichess_reconnect" | null,
      "action_label":  <string | null>,
      "metadata":      <object>,
      "created_at":    <ISO-8601 string>,
      "read_at":       <ISO-8601 string | null>
    }
  ],
  "unread_count": <int>
}
```

- `unread_count` is the bell-badge value: unread visible rows at
  priority medium and above (spec §5.6).  Low-priority rows appear in
  the feed but never badge.
- `action` is an app-internal deep-link **key**, not a URL — the
  client maps `open_history` to the game-history sheet and
  `lichess_reconnect` to the Lichess connect sheet.  Unknown keys must
  render as informational rows (no CTA), so new types can ship
  server-first.
- `metadata` carries per-type context: `{"games_analyzed": <int>}` for
  `game_analyzed`, `{"lichess_username": <string>}` for the reconnect
  alert.

### `POST /notifications/{id}/read`

Marks one row read (idempotent — the first `read_at` wins).

```json
{ "read": true, "unread_count": <int> }
```

### `POST /notifications/read-all`

Marks every **visible** unread row read; rows stay in the feed.

```json
{ "marked": <int>, "unread_count": 0 }
```

### `POST /notifications/{id}/dismiss`

Soft-deletes one row from the feed (idempotent; the row stays in the
DB).  Dismissing does not mark read — the badge count simply stops
seeing the row because visibility filters run first.

```json
{ "dismissed": true, "unread_count": <int> }
```

### Errors

| Status | Cause |
|--------|-------|
| `400`  | `id` longer than 64 chars. |
| `401`  | Missing or invalid `Authorization` header. |
| `403`  | Notification belongs to another player (probe-visible, same convention as §39). |
| `404`  | Notification does not exist. |
| `429`  | Mutation rate limit exceeded (Shape B). |

### Producer semantics (server-side, no HTTP surface)

- `game_analyzed` — fired when an **async** import job succeeds with
  `analyzed > 0` (§31); the v1 sync import (§30) deliberately does not
  notify (its caller renders the summary inline).  A second job
  finishing within 60 minutes **merges** into the still-unread row
  (count summed, `created_at` reset); a read row is never merged into.
  Expires 30 days after (re)creation.
- `system_alert` / `lichess_reconnect` — fired when any import 404s on
  the linked account; at most one live copy per player.  No expiry
  ("until resolved"): it is auto-dismissed by the next clean import,
  re-link, OAuth sign-in auto-link, or unlink — see §29's
  `disconnected` field for the paired status flag.

---

## 41. `DELETE /auth/me`

**Host:** `llm/seca/auth/router.py`
**Auth:** `Authorization: Bearer <token>` required (`get_current_player`)
**Rate limit:** 5 / minute

Erase the authenticated account — GDPR Art. 17 right to erasure.
Deletes the `players` row and **every** linked row across the schema
(sessions, games + moves, game events + finish results + reviews, chat
history, study plans + puzzles, training history, repertoire,
skill/bandit records, analytics, usage counters, feedback,
notifications, Lichess link + import jobs) via the single deletion
authority in `llm/seca/auth/erasure.py` (`purge_player_data` —
children before parents, one transaction, per-table counts logged
server-side).  Irreversible — the client MUST gate the call behind an
explicit confirmation UI.  The Android surface is Settings › Account ›
**Delete account** → `AccountFlows.confirmAndDeleteAccount` ("Are you
sure" dialog; `SettingsDeleteAccountSourcePinTest` pins that the
destructive call is reachable only from the dialog's positive button,
and that a failed deletion never wipes local state).  iOS has no entry
point yet (Phase-0 parity gap).

Identity proof is the bearer token alone (no password re-entry):
Lichess sign-in accounts have no usable password, so the standing
token is the uniform credential for both account types.

The token that authorised the call dies with its session: the post-2xx
rotation middleware hits `rotate_session_token`'s documented
deleted-session no-op (an `X-Auth-Token` header on the response, if
set, names a session that no longer exists and is unusable), and any
replay of the old token returns `401`.

A new player-linked table added without registering it in the erasure
plan fails `test_auth_account_deletion.py`'s metadata-discovery
tripwire in CI — the plan cannot silently fall behind the schema (the
`notifications` table from §40 was caught exactly this way while this
endpoint was in review).

### Request body

Empty.

### Response

```json
{ "status": "deleted" }
```

### Errors

- `401` — missing / malformed / expired token, or session already
  revoked (including a repeat of this call: the second attempt cannot
  authenticate, which makes erasure trivially idempotent).
- `429` — rate limit exceeded (Shape B).

---

## 42. `GET /auth/me/export`

**Host:** `llm/seca/auth/router.py`
**Auth:** `Authorization: Bearer <token>` required (`get_current_player`)
**Rate limit:** 3 / minute (the heaviest read in the API — one SELECT
per player-linked table)

GDPR Art. 15 (access) + Art. 20 (portability): download everything the
service stores about the authenticated account as one JSON document.

Scope is structurally identical to erasure (§41): both endpoints read
the same plan (`erasure.player_data_plan`), so the metadata-discovery
tripwire that keeps §41 complete keeps this document complete in the
same breath.  Credential columns are withheld by the explicit policy in
`llm/seca/auth/export.py::COLUMN_EXCLUSIONS` (`players.password_hash`,
`sessions.token_hash` / `previous_token_hash`); a pattern guard in
`test_auth_data_export.py` forces any future secret-smelling column
(password/token/secret in the name) into that policy — nothing can
leak into the export by default.

### Request body

Empty.

### Response

```json
{
  "export_version": 1,
  "generated_at": "<ISO-8601 UTC>",
  "player_id": "<uuid>",
  "contents": "GDPR Art. 15/20 data export — every table linked to this account",
  "data": { "<table_name>": [ { "<column>": <value>, … } ], … }
}
```

- `data` carries one key per player-linked table (26 as of this
  writing — `players`, `game_events`, `chat_turns`, …), each a list of
  row objects; empty tables are present as `[]`, never omitted, so the
  key set always matches the erasure scope.
- Datetimes are ISO-8601 strings; JSON columns arrive as nested
  objects.  Adding tables or columns is NOT an `export_version` bump —
  consumers must treat `data` as an open mapping.
- No pagination in v1: response size is bounded by real usage (free
  tier: 1 game/day).  Revisit if exports exceed a few MB.

The Android surface is Settings › Account › **Download my data** →
`DataExportFlows.startDownload`: fetch-then-pick via the Storage
Access Framework (`CreateDocument("application/json")`, zero
permissions on minSdk 26+), saving the server's bytes verbatim as
`cereveon-data-export-<date>.json`.
`SettingsDownloadDataSourcePinTest` pins that a failed fetch never
opens the picker (and never creates a file);
`AuthExportDataIntegrationTest` pins the wire contract, including
that the raw body round-trips unparsed.  iOS has no entry point yet
(Phase-0 parity gap).

### Errors

- `401` — missing / malformed / expired token.
- `429` — rate limit exceeded (Shape B).

---

## 43. `GET` / `POST /delete-account` (+ Lichess sub-routes)

**Host:** `llm/seca/auth/web_deletion.py`
**Auth:** none — this is the **public web** account-deletion surface
Google Play's User Data policy requires *in addition to* the in-app
path (§41): a page reachable WITHOUT the app, for users who have
uninstalled.  Served top-level (no `/auth` prefix) so the URL is short
enough for the Play Console Data safety form:
`https://<host>/delete-account`.
**Rate limit:** page 20/min; password submit 5/min; Lichess sub-routes
10/min (all per-IP).

Ownership is proven, then the account is erased on the spot via the
same `erasure.purge_player_data` authority as §41 — never deleted from
a typed-in email alone.

Routes:

- `GET /delete-account` — renders the self-contained HTML page (inline
  CSS, no JS, no external assets; `noindex`). GET is never destructive.
- `POST /delete-account` — password accounts. urlencoded body `email` +
  `password` + `confirm=yes` (the checkbox). `AuthService.verify_credentials`
  (timing-safe, issues **no** session) → purge. Returns the "deleted"
  page on success; re-renders the form with a generic message on bad
  credentials (`401`) or a missing field / unchecked box (`400`) — no
  email enumeration, no reflected input (no XSS surface). Credential
  verify + purge run in a threadpool (PBKDF2 is ~100 ms).
- `GET /delete-account/lichess/start` — begins a Lichess authorization-
  code + PKCE flow with an `https://<host>/delete-account/lichess/callback`
  redirect_uri (distinct from the app's custom-scheme one; the exchange
  reuses `exchange_authorization_code`'s new `redirect_uri` override).
  The PKCE verifier + a CSRF `state` are stored in a signed
  (HS256/`SECRET_KEY`), HttpOnly, Secure, SameSite=Lax cookie scoped to
  `/delete-account`; `302` to `lichess.org/oauth`.
- `GET /delete-account/lichess/callback?code=&state=` — verifies the
  cookie + `state` (rejects missing/expired/mismatch with `400` before
  any upstream call), exchanges the code, fetches the account, matches
  `players.lichess_user_id`, purges, and revokes the Lichess token. `404`
  when the proven Lichess identity has no linked Cereveon account; `502`
  on a Lichess upstream failure.

Config: `PUBLIC_BASE_URL` (default `https://cereveon.com`) builds the
Lichess callback URL — an env var, not `request.base_url`, so a proxied
`http` scheme can never leak into the OAuth `redirect_uri`.

Lichess-only accounts (synthetic `email = "lichess:<id>"`, unusable
password hash) can only be deleted through the Lichess sub-routes — the
password form can never match them.

---

## 44. `GET /privacy` (+ `/privacy-policy` alias)

**Host:** `llm/seca/legal/router.py`
**Auth:** none — the **public** privacy policy Google Play requires as a
live URL in the store listing + Data safety form.
**Rate limit:** none (static, cacheable — `Cache-Control: public,
max-age=3600`).

Serves the published privacy policy as a self-contained static HTML
page (`llm/seca/legal/privacy_policy.html`, read once at import; no
auth, no DB, no external assets).  `/privacy` is canonical;
`/privacy-policy` is an alias so either works in a store listing.

This is the PUBLISHED policy, distinct from the internal working draft
in `docs/PRIVACY_POLICY.md` (which carries compliance-gap tracking + a
claim→code evidence map that must never reach users).
`test_privacy_policy_page.py`'s anti-leak guard structurally prevents
publishing the draft — the served page must contain no placeholder /
draft / annex markers.

---

## 45. `POST /coach/report`

**Host:** `llm/seca/moderation/router.py`
**Auth:** `Authorization: Bearer <token>` required (`get_current_player`)
**Rate limit:** 10 / minute

Flag a coach (AI-generated) message as offensive or harmful — the
in-app reporting affordance Google Play's **AI-Generated Content
policy** requires (users must be able to report offensive AI output
without leaving the app; reports inform moderation).  Distinct from
`POST /game/coach-feedback`, which is a helpful/not-helpful *quality*
signal.

### Request body

```json
{
  "content": <string>,              // the reported coach text (required, ≤ 8000)
  "surface": "chat" | "live_move" | "review" | "study_plan" | "other",
  "fen":     <string | null>,       // board context, ≤ 200 (optional)
  "reason":  <string | null>        // user's note, ≤ 1000 (optional)
}
```

`content` is trimmed and must be non-empty; `surface` must be in the
closed vocabulary; `fen` / `reason` are omitted from the Android wire
when null.  The reported text is untrusted user-flagged content: stored
verbatim in `content_reports` (an operator-reviewed queue, `reviewed =
0`), **never logged** (the log line carries only player id + surface +
length), and never read back into any coaching / prompt / adaptation
path.  Deleted with the account (in the erasure + export plans).

### Response

```json
{ "status": "received", "id": "<uuid>" }
```

### Errors

- `401` — missing / invalid token.
- `422` — empty content, unknown surface, or an over-cap field.
- `429` — rate limit exceeded (Shape B).

The Android surface is the **Report (flag)** icon on each coach message
in the chat panel (`ChatAdapter` → `ChatBottomSheet.showReportDialog`);
`ReportContentSourcePinTest` pins that the tap only opens a confirmation
dialog and the POST fires from its positive button.

---

## 40. `POST /billing/revenuecat/webhook`

**Host:** `llm/seca/billing/router.py`
**Auth:** shared-secret `Authorization` header (RevenueCat), **not** JWT / X-Api-Key
**Rate limit:** 120 / minute

The subscription **source of truth**. The app purchases through the
RevenueCat SDK; RevenueCat validates receipts with the stores and POSTs
lifecycle events here. The server maps `event.app_user_id` (the app sets
it to the server **player id**) to the player and flips `Player.plan` via
`entitlements.set_plan` — keeping enforcement server-authoritative (a
modified client cannot fake pro) and auto-downgrading on expiry.

### Auth

RevenueCat sends the value configured as the webhook's *Authorization
header* (dashboard → Integrations → Webhooks) in the `Authorization`
header. The server compares it (constant-time) to `REVENUECAT_WEBHOOK_AUTH`.

- `503` — `REVENUECAT_WEBHOOK_AUTH` unset (fail closed).
- `401` — header missing or mismatched. Plan not touched.

### Request body

RevenueCat's webhook envelope; the fields consumed:

```json
{
  "api_version": "1.0",
  "event": {
    "type": "INITIAL_PURCHASE | RENEWAL | CANCELLATION | EXPIRATION | ...",
    "app_user_id": "<server player id>",
    "entitlement_ids": ["pro"],
    "environment": "PRODUCTION | SANDBOX"
  }
}
```

### Behaviour

| Event type | Action on the `"pro"` entitlement |
|-----------|-----------------------------------|
| `INITIAL_PURCHASE`, `RENEWAL`, `UNCANCELLATION`, `PRODUCT_CHANGE`, `NON_RENEWING_PURCHASE`, `SUBSCRIPTION_EXTENDED` | **grant** → `plan = "pro"` |
| `EXPIRATION` | **revoke** → `plan = "free"` |
| `CANCELLATION` (auto-renew off, still entitled), `BILLING_ISSUE` (grace), `TEST`, others | **no-op** (200) |

### Response

Always `200` for an authenticated, well-formed event — including no-op
types, an unmatched `app_user_id` (`{"action": "player_not_found"}`), or
an anonymous `$RCAnonymousID:` id (`{"action": "skipped_anonymous"}`) — so
RevenueCat does not retry a permanent condition. Only a DB write failure
returns `500` (RevenueCat retries). Idempotent: a duplicated grant leaves
`plan = "pro"`. Independent of `SECA_ENTITLEMENTS_ENFORCED` — the plan
column always tracks reality so enforcement (whenever on) reads the right
tier.

```json
{ "status": "ok", "action": "granted | revoked | ignored | player_not_found | skipped_anonymous", "plan": "pro | free" }
```

---

## Error responses

The API emits **two distinct error-body shapes** that any client (the
Android app, server-to-server callers, future SDKs) must handle.  The
duality is historical — the body-size middleware and slowapi
rate-limit handler predate the API_CONTRACTS work and use a different
key from the FastAPI standard.  Until they're consolidated (a
coordinated Android release is needed), both shapes are part of the
contract.

### Shape A — FastAPI / Pydantic standard

```json
{ "detail": <string | object | array> }
```

- `detail` is a **string** for most `raise HTTPException(...)` paths
  (e.g., `{"detail": "invalid FEN"}`).
- `detail` is an **array of `{loc, msg, type}` records** for Pydantic
  422 failures — one entry per failed field.  Example:

  ```json
  { "detail": [{"loc": ["body", "fen"], "msg": "invalid FEN", "type": "value_error"}] }
  ```

- `detail` is a **string** with a structured one-line message for
  API-version mismatches: `{"detail": "X-API-Version mismatch: client
  sent 'X', server supports [Y] (current: 'Z'). Update the client to a
  supported version."}`.

### Shape B — Custom-middleware shape

```json
{ "error": <string> }
```

Used only by three surfaces, all of which return a constructed
`JSONResponse` rather than `raise HTTPException(...)`:

- `_LimitBodySize` middleware (`llm/server.py`) — 411 / 413 / 400 for
  Content-Length-related rejection.
- `rate_limit_handler` (`llm/server.py`) — 429 for slowapi rate-limit
  exceedance.
- `_chat_limit_response` (`llm/server.py`) — 402 for an over-quota chat
  turn on `/chat` and `/chat/stream` (entitlements; dormant unless
  `SECA_ENTITLEMENTS_ENFORCED` is on). Carries **additional contract
  keys** beyond `error`: `plan`, `limit`, `used`, `upgrade.product` —
  the one Shape B body where clients read more than the `error` string.

### Status-code → shape mapping

| HTTP | Shape | Body example | Trigger |
|------|-------|--------------|---------|
| 400 | A | `{"detail": "invalid FEN"}` | `raise HTTPException(400)` paths: bad FEN / game_id / eco / text-field constraints. |
| 400 | A | `{"detail": "X-API-Version mismatch: …"}` | `api_version_gate` middleware: client `X-API-Version` not in `API_VERSIONS_SUPPORTED`. |
| 400 | B | `{"error": "Invalid Content-Length"}` | `_LimitBodySize` middleware: Content-Length header doesn't parse as int. |
| 401 | A | `{"detail": "Invalid or expired token"}` / `{"detail": "Missing token"}` / `{"detail": "Invalid credentials"}` | `get_current_player` / `logout` / `login` failures; raw `Authorization` header parse on `/auth/logout`. |
| 402 | B | `{"error": "chat_daily_limit", "plan": "free", "limit": 3, "used": 3, "reset_at": "2026-07-18T14:03:22.511000", "upgrade": {"product": "pro_monthly"}}` | Entitlements chat quota exhausted on `/chat` / `/chat/stream` (only when `SECA_ENTITLEMENTS_ENFORCED` is on). Rolling 24h window (§5), not calendar-daily; `reset_at` (nullable, ISO-8601 UTC) drives the client's live countdown. The extra keys are contract, not decoration — the paywall sheet renders them. No `X-Auth-Token` header (non-2xx). |
| 402 | B | `{"error": "game_daily_limit", "plan": "free", "limit": 1, "used": 1, "reset_at": "2026-07-18T14:03:22.511000", "upgrade": {"product": "pro_monthly"}}` | Free-tier game limit on `/game/start` (§11), rolling 24h window (not calendar-daily), only when `SECA_ENTITLEMENTS_ENFORCED` is on. Hard block: no `game_id` returned. Client shows a non-dismissible paywall with a `reset_at` countdown. No `X-Auth-Token` header (non-2xx). |
| 402 | A | `{"detail": "purchase not active (SUBSCRIPTION_STATE_EXPIRED)"}` | `/billing/google/verify` (§36): Google's verdict is that the token carries no entitlement. Plan not flipped. |
| 502 | A | `{"detail": "purchase verification temporarily unavailable"}` | `/billing/google/verify` (§36): Google OAuth / Play API unreachable or abnormal — retry later. |
| 503 | A | `{"detail": "purchase verification not configured"}` | `/billing/google/verify` (§36): `GOOGLE_PLAY_*` service-account env vars unset on this deploy. |
| 403 | A | `{"detail": "not your game"}` | Authenticated player operating on another player's resource (game lifecycle). |
| 404 | A | `{"detail": "no active game"}` / `{"detail": "game not found"}` / `{"detail": "opening not found"}` | Resource-not-found across `/game/*`, `/repertoire/*`. |
| 409 | A | `{"detail": "game already finished"}` | Game-lifecycle conflict (checkpointing a finished game). |
| 411 | B | `{"error": "Content-Length header required"}` | `_LimitBodySize` middleware: POST/PUT/PATCH without `Content-Length`. |
| 413 | B | `{"error": "Request body too large"}` | `_LimitBodySize` middleware: Request body exceeds 512 KB (the `_MAX_BODY_BYTES` cap; applies on every endpoint). |
| 422 | A | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}, …]}` | Pydantic validation failure (FastAPI default). |
| 429 | B | `{"error": "Too many requests"}` | `rate_limit_handler` (slowapi). |
| 500 | A | `{"detail": "Internal Server Error"}` / `{"detail": "Server misconfiguration"}` | Unhandled server error (FastAPI default).  See "Errors that DON'T propagate" below for the Mode-2 pipeline cases that look like 500s but never reach Android. |

### Client parsing recipe

For both shapes the message lives behind exactly one of two keys.
Android's HTTP layer should try `body["detail"]` first; on absence,
fall back to `body["error"]`; on absence of both, surface the HTTP
status code itself.  Pydantic 422's array form is the only case where
`detail` is structured rather than scalar — clients that want
field-level error reporting can iterate when `isinstance(detail, list)`.

### Errors that DON'T propagate to Android

These exception classes exist on the server and would surface as 500
if not handled, but the live request paths catch and recover before
the bytes leave the route:

- **`ExplainSchemaError`** (`llm/rag/validators/explain_response_schema.py`) —
  raised by `validate_chat_response` / `validate_live_move_response`
  when the LLM pipeline's output drifts from the boundary schema.  The
  `/chat`, `/chat/stream`, `/live/move` handlers catch it and re-run
  the pipeline with `force_deterministic=True`, which is constructed
  to pass every gate by construction.  Net effect on Android: the
  response is a deterministic-fallback string in the normal `200`
  shape, not a 500.  Closes the cascading-401 lockout pinned by
  `[[project-token-rotation-post-2xx]]`.
- **`OutputFirewallError`** (`llm/rag/safety/output_firewall.py`) —
  raised by `check_output` for PII / identity / prompt-leak / harmful
  patterns in LLM output.  Caught inside `chat_pipeline._build_chat_llm`
  and `rag/deploy/embedded.py`; the offending reply is replaced with
  `"I cannot process this request."` before the route returns.

### Headers on error responses

All error responses (4xx and 5xx) carry the same security headers as
successful responses (CSP, HSTS, X-Frame-Options, etc., from
`add_security_headers` middleware).  Additionally:

- **`X-API-Version`** — always present (set by `api_version_gate` /
  `rate_limit_handler` for the 400-on-mismatch and 429 paths
  explicitly; via the standard response pipeline elsewhere).
- **`X-API-Versions-Supported`** — same as the success response.
- **`X-Auth-Token`** rotation — **NOT** emitted on failure paths
  (401 / 403 / 422 / 5xx).  Defends against a hostile client harvesting
  tokens by probing.  See `commit_pending_auth_rotation` middleware
  rationale in §10 of this document.
