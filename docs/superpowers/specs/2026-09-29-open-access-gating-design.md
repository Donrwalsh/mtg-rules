# Open access with cost gating

Status: design agreed, awaiting implementation plan
Date: 2026-09-29
Branch: `feature/open-access` (to be cut from `main`)

## Purpose

Take the production site out from behind HTTP basic auth so anyone can ask
a question, while making the worst-case Gemini spend **predictable**:
never more than about $1 per UTC day, however many people (or IPs) show
up. Per-IP limits keep one visitor from using the whole day's budget;
the global cap is the guarantee, since per-IP limits alone can't
bound total spend against someone rotating addresses.

Core decisions:

- A **global daily dollar cap** computed from Gemini's real token counts.
- **Per-IP quotas** in two layers: nginx for floods, Postgres for LLM
  answers.
- **Degrade, don't break**: over a limit, a visitor still gets
  retrieval-only results (matched rules, cards, rulings) at zero token
  cost.
- An **answer cache**, clearly labelled in the UI, so popular questions
  cost one LLM call.
- A hidden **admin login** (ported from the Connections app) for
  history, usage, and quota exemption.

## Investigation findings

1. **No per-request cost bound.** `QueryRequest.query` has no length
   limit and `generation_max_tokens` defaults to `None`. Every generated
   answer carries a large context (up to `hybrid_top_k`=10 vector hits,
   `rules_top_k`=5 rule hits, `card_ruling_limit`=20 rulings per matched
   card, plus keyword subrules).
2. **Token usage is thrown away.** `GeminiAnswerer.generate()` returns
   only the answer text; `usageMetadata` is discarded.
3. **Thinking is at the model default.** No `thinkingConfig` is sent.
   Gemini bills thinking tokens as output and counts them against
   `maxOutputTokens`, so thinking dominates per-call cost variance and a
   tight output cap can starve the visible answer.
4. **Evals have never varied thinking.** No experiment or setting
   touches it. The eval answer cache (`evals/src/mtg_evals/cache.py`)
   keys on `generator_label()` + generation overrides. If a thinking
   default changed without entering `generator_label()`, baseline runs
   would silently reuse answers generated under the old thinking level.
5. **Client IP plumbing already works.** Traefik → nginx
   (`$proxy_add_x_forwarded_for`) → uvicorn `--proxy-headers
   --forwarded-allow-ips='*'`. nginx itself sees Traefik's address as
   `$remote_addr`, so nginx-level per-IP limits need the `real_ip`
   module first.
6. **Production has Postgres, not Redis.** Counters live in Postgres.
7. **`/api/v1/query` is sync, one uvicorn worker, CPU-bound embedding on
   2 shared vCPUs.** Retrieval-only spam costs CPU the neighbouring
   apps share, not tokens, so the nginx flood limits cover all queries.
8. **History is public.** `GET /api/v1/queries` and the `/history` page
   would expose every visitor's questions once basic auth is gone.
9. **Parsed JSONL on the server.** The parsed-data volume holds the
   latest `cards_*.jsonl` (~17 MB) and `rules_*.jsonl` (~1.2 MB); the
   rulings file (~29 MB) stays local. The backend needs both at startup
   (card matcher, rules index, keyword matcher). Small next to Qdrant's
   storage; not worth changing.
10. **Data updates reach production two ways.** A new
    `rules_*.jsonl` / `cards_*.jsonl` (date-stamped) *and* a Qdrant
    snapshot. Rulings exist in production only inside Qdrant, so
    file names alone can't detect every data change.
11. **Connections admin pattern.** One password env var; HMAC-signed
    `admin_session` cookie (`<expiresAt>.<sig>`, key =
    `sha256("admin:" + password)`), HttpOnly, SameSite=Strict;
    `POST /auth/login`, `POST /auth/logout`, `GET /auth/me →
    {isAdmin}`; cookie-authenticated mutating calls also require an
    `X-Admin-Request: 1` marker header (CSRF defense in depth). See
    `Connections/backend/src/modules/auth/`.

## Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Budget | App-enforced global cap of **$1/UTC day**, plus a Google Cloud budget *alert* (~$30/month) as a backstop. |
| 2 | Threat model | Design for a motivated scripter with one or a few IPs; the global cap absorbs distributed abuse. |
| 3 | Where limits live | nginx `limit_req`/`limit_conn` for floods + Postgres for per-IP LLM quotas and the global cap. No Redis. |
| 4 | Over a limit | LLM limits → retrieval-only with an explanation. nginx flood limits → 429. |
| 5 | Per-request bounds | Query ≤ 500 chars (422 otherwise). Output cap ~2048 tokens total. |
| 6 | History | Admin-only, removed from the public UI. |
| 7 | Answer cache | Yes, exact match on the normalized query; clearly labelled on screen. |
| 8 | Measuring spend | Record real `usageMetadata` × configured prices. Before each LLM call, halt if today's recorded spend ≥ cap. No worst-case reservation; in-flight overshoot of a few cents is accepted. |
| 9 | Thinking | Low thinking level in production. New overridable setting, part of `generator_label()`, with a `thinking-low` eval experiment. |
| 10 | Per-IP quotas | 20 LLM answers/day, ≤ 5 per 10 minutes; nginx ~1 req/s, burst 5, 2 concurrent; IPv6 bucketed by /64; UI shows answers remaining. Cache hits are free. |
| 11 | CAPTCHA | None for now. |
| 12 | Admin | Port the Connections pattern; admin exempt from per-IP limits and the global cap, but usage still recorded. Replaces nginx basic auth. |
| 13 | Monitoring | Admin usage panel + Google Cloud budget alert. No push alerts yet. |
| 14 | Cache details | Normalize (lowercase, collapse whitespace, trim trailing punctuation); cache successful answers only; badge "Cached answer · first generated <date>"; only admin can force a fresh answer. |
| 15 | Eval gate | Ship low thinking regardless of eval results; the eval run is informational. |
| 16 | Data version | `data_version` marker file written by `sync_data.py` into the parsed-data volume; any sync invalidates the cache. Fallback: latest file names. |
| 17 | IP storage | Raw bucket (IPv4 address or IPv6 /64). No block list. |
| 18 | Rollout | Build and deploy behind existing basic auth, verify in production, then remove basic auth in a separate change. |
| 19 | Retention | Keep usage rows (with IPs) indefinitely **for now**; re-evaluate before 2026-10-29. |
| 20 | Eval run | Claude runs the `thinking-low` experiment at the end of implementation and reports the numbers. |

## Design

### Request flow (`POST /api/v1/query`)

1. **Validate.** `query` gets `max_length` = `max_query_chars` (500);
   longer → 422. `overrides` stay eval-mode-only (unchanged).
2. **Identify the caller.** `ip_bucket` = client IPv4 address, or the
   client IPv6 address masked to /64 (`ipaddress` stdlib). `is_admin` =
   valid `admin_session` cookie.
3. **Cache lookup** (when `generate` is true, not eval mode, no
   overrides, and not an admin `fresh` request). On a hit, return the
   stored response with `cached = {generated_at}`, record a `cached`
   usage row, save history, done: no retrieval, no LLM, no quota
   consumed.
4. **Gate.** Unless admin:
   - global: `SUM(cost_usd)` for today (UTC) ≥ `daily_budget_usd` →
     `degraded = "global_budget"`;
   - per IP: `generated` rows for this bucket today ≥
     `ip_daily_llm_limit`, or in the last `ip_window_minutes` ≥
     `ip_window_llm_limit` → `degraded = "ip_quota"`.
5. **Retrieve** exactly as today.
6. **Generate** unless degraded (or `generate` is false). Record a
   usage row with the real token counts and cost. A failed call records
   a row with whatever usage is known (zero if none) and the error.
7. **Store in cache** if the answer is non-null and non-empty.
8. **Save history** as today (with the new `cached` flag).
9. **Respond** with the existing fields plus `cached`, `degraded`, and
   `answers_remaining` (null for admin).

Cost per call = `promptTokenCount × input_price +
(candidatesTokenCount + thoughtsTokenCount) × output_price`, prices in
USD per million tokens from settings. Verify current gemini-3.5-flash
pricing and the exact `usageMetadata` field names at implementation
time.

### Data model (Alembic migration 0003)

**`llm_usage`**: one row per `/query` call that asked for an answer:

| column | notes |
|---|---|
| `id`, `created_at` (timestamptz, indexed) | |
| `ip_bucket` (text, indexed with `created_at`) | IPv4 or IPv6 /64 in CIDR form |
| `is_admin` (bool) | |
| `outcome` (text) | `generated` \| `cached` \| `degraded_ip` \| `degraded_global` \| `error` |
| `model` (text) | |
| `input_tokens`, `output_tokens`, `thinking_tokens` (int) | 0 when no call was made |
| `cost_usd` (numeric) | 0 when no call was made |

Admin rows count toward today's spend total (so the panel is honest)
but the admin is never *blocked* by it.

**`answer_cache`**:

| column | notes |
|---|---|
| `key` (text PK) | sha256 of the parts below |
| `normalized_query` (text) | for the admin panel |
| `response` (jsonb) | full `QueryResponse` (answer, citations, rule_references, citation_stats, results) |
| `created_at` (timestamptz) | shown in the badge |
| `hit_count` (int) | for the panel |

Cache key parts: normalized query, `PROMPT_VERSION`,
`generator_label()` (model + thinking level), `data_version`, and a
fingerprint of the retrieval settings (`hybrid_*`, `rules_top_k`,
`card_ruling_limit`, `collection_name`), so a config change or data
sync never serves an answer built from different context. Storing the
whole response (not just the answer text) keeps the `[n]` citation
numbers matched to the exact sources they referred to.

**`query_history`** gains a `cached` boolean (default false).

### Settings (`mtg_api/config.py`)

| setting | default | notes |
|---|---|---|
| `max_query_chars` | 500 | |
| `daily_budget_usd` | 1.0 | |
| `gemini_input_price_per_mtok`, `gemini_output_price_per_mtok` | none | Required when the budget is enforced; startup fails without them. |
| `ip_daily_llm_limit` | 20 | |
| `ip_window_llm_limit` / `ip_window_minutes` | 5 / 10 | |
| `generation_thinking_level` | `None` (model default) | Production sets `low`. Added to `OVERRIDABLE_SETTINGS` and `GENERATION_SETTINGS`; included in `generator_label()` (e.g. `gemini:gemini-3.5-flash:think=low`). Gemini 3.x takes `thinkingConfig.thinkingLevel`; confirm against the API at implementation time. |
| `generation_max_tokens` | unchanged (`None`) | Production sets 2048. |
| `admin_password` | empty | Empty = admin login disabled. |
| `answer_cache_enabled` | true | Always bypassed in eval mode. |

Keeping `generation_thinking_level` at `None` in the dev/eval default
keeps baseline eval runs comparable to past ones; production turns it
on through compose.

### Gemini client

`GeminiAnswerer.generate()` returns a small result object (`text`,
`input_tokens`, `output_tokens`, `thinking_tokens`) instead of a bare
string, and sends `thinkingConfig` when a level is set. Callers (the
query endpoint, eval-mode fields) are updated accordingly.

### Data version

`deploy/sync_data.py` adds a `data_version` file (UTC ISO timestamp of
the sync) to the tar it already streams into the parsed-data volume,
and its cleanup (`rm -f /d/cards_*.jsonl /d/rules_*.jsonl`) also
removes the old marker. The backend reads it once at startup (it already
restarts after every sync). With no marker (dev stack), the data version
is the latest cards and rules file names. Any sync therefore invalidates
every cached answer, covering rules, oracle text, rulings, and
re-embeds alike.

### Admin auth (port of Connections)

- `POST /api/v1/auth/login` `{password}`: compares with
  `hmac.compare_digest`, sets `admin_session` = `<expiresAt>.<hmac>`
  (HttpOnly, SameSite=Strict, Secure behind HTTPS).
- `POST /api/v1/auth/logout`, `GET /api/v1/auth/me → {is_admin}`.
- A FastAPI dependency `require_admin` (cookie + `X-Admin-Request: 1`
  header) gates `GET /api/v1/queries` and the new
  `GET /api/v1/admin/usage`.
- `POST /api/v1/query` accepts `fresh: true` only from an admin (skip
  the cache lookup, overwrite the entry).
- nginx applies a strict separate rate limit to
  `/api/v1/auth/login` (for example 5/min per IP) against password
  guessing.
- Production: `MTG_API_ADMIN_PASSWORD: ${SERVICE_PASSWORD_ADMIN}`
  (Coolify-generated).

### Admin usage endpoint and panel

`GET /api/v1/admin/usage` returns today's spend against the cap, the last
7 days' spend, outcome counts (generated / cached / degraded_ip /
degraded_global / error), cache hit rate, and the top IP buckets today
(requests, answers, spend).

### nginx

- `real_ip`: `set_real_ip_from` the Docker/Coolify network ranges,
  `real_ip_header X-Forwarded-For`, so `$binary_remote_addr` is the
  visitor.
- `limit_req_zone` ~1 r/s burst 5 and `limit_conn` 2 on `/api/`;
  a stricter zone on `/api/v1/auth/login`; `limit_req_status 429`.
- nginx limits are per address (no /64 masking); they are flood
  protection only. The /64 bucketing lives in the app quotas.

### Frontend (`mtg-web`)

- Input `maxlength` 500 plus a character counter near the limit.
- **Cached badge** on the answer: "Cached answer · first generated
  <date>". Admins also see a "Get a fresh answer" button.
- **Degraded notice** above the sources: `ip_quota` → "You've used
  today's AI answers; here are the matching rules and rulings. Resets
  at <UTC midnight in local time>." `global_budget` → "AI answers are
  paused for today…".
- Quiet "N AI answers left today" line.
- Unlinked `/login` route. `/auth/me` at app load; the History and
  Usage nav links and routes appear only for the admin.
- A 429 from nginx shows a "slow down" message instead of a generic
  error.

### Evals

- `evals/experiments/thinking-low.yaml` overriding
  `generation_thinking_level: low`.
- Baseline runs stay on the model default (see Settings), so the
  comparison is default vs low. The report should add per-answer cost
  from the new usage numbers if the eval-mode response exposes them
  (add token counts to the eval-only response fields).

### Provider backstop (manual)

A Google Cloud billing budget alert on the Gemini project at ~$30/month.
It alerts; it does not cap.

## Rollout

1. Implement on `feature/open-access`, tests alongside, deploy with
   basic auth still on.
2. Verify in production:
   - a normal answer records a `generated` usage row with non-zero
     tokens and cost;
   - repeat question → cached badge, `cached` row, no new spend;
   - `MTG_API_DAILY_BUDGET_USD=0.01` → the next non-admin question
     degrades to `global_budget`; admin still gets answers; restore
     the value;
   - lower `ip_window_llm_limit` temporarily → `ip_quota` degradation;
   - a client-supplied `X-Forwarded-For` header does not change the
     recorded `ip_bucket` (Traefik must strip it);
   - `/api/v1/queries` and `/api/v1/admin/usage` → 401/403 without the
     admin cookie; `/login` works; nav links appear;
   - a query over 500 chars → 422; burst requests → nginx 429;
   - a `sync_data.py` run writes `data_version` and old cache entries
     stop matching.
3. Set up the Google Cloud budget alert.
4. Separate small change: remove `MTG_WEB_AUTH_*` from
   `docker-compose.prod.yml`, delete `40-basic-auth.sh` and the
   `include /etc/nginx/auth.conf`, update the README.
5. Run the `thinking-low` eval experiment and report quality and cost
   (informational; it does not gate shipping).

## Out of scope / revisit later

- CAPTCHA / Turnstile (add if the usage panel shows abuse).
- Push alerts (email or ntfy) at a spend threshold.
- IP block list (a one-line nginx `deny` if ever needed).
- Usage-row retention: **re-evaluate before 2026-10-29**.
- Semantic (near-duplicate) answer caching.
- Tuning thinking level beyond `low` based on eval results.
