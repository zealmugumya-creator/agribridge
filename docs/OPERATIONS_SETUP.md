# AgriBridge — Operations & Setup Guide

How to activate every integration that is **implemented but gated on credentials**, plus how to run the backend, worker, and migrations safely. All secrets are read from the environment only (Rule 4); variable **names** are in `.env.example` with no values.

---

## 0. Prerequisites
- Python 3.12.7 (matches Render). Install runtime deps: `pip install -r requirements.txt`; dev deps: `pip install -r requirements-dev.txt`.
- The Supabase project (`vyrctsiyaihsysgpozdm`) service-role key, and the DB connection string for migrations.
- A Render service for the API (`agribridge-1`) and one for the worker (§5).

---

## 1. Environment variables (Render → Environment)
Confirm these exist. See `.env.example` for the full list and semantics.

**Core (required in production — the app fails closed without them):**
- `JWT_SECRET` — long random string. **Must be identical across all API workers** (a per-boot random value breaks sessions across the 2 gunicorn workers — audit H1).
- `SUPABASE_URL`, `SUPABASE_KEY` (service role).

**Admin:**
- `ADMIN_ALLOWED_UUIDS` — comma-separated verified admin UUIDs (bootstrap allowlist used before the `user_roles` row exists).
- `ADMIN_PASSWORD` — legacy shared-password console only. Rotate off the documented default (`agribridge2026`) and migrate to Supabase-Auth admin login (§3).

**Integrations (each stays a safe no-op until set):**
- `DISCORD_WEBHOOK_URL` (§4) · `SENTRY_DSN` (§6) · `FCM_CREDENTIALS_JSON`/`FCM_SERVICE_ACCOUNT_PATH`/`FCM_PROJECT_ID` (§7) · `FLW_SECRET_KEY`/`FLW_WEBHOOK_HASH` (§8) · Africa's Talking `AT_*` · `GROQ_API_KEY`/`GEMINI_API_KEY`.

**Policy:**
- `STOCK_FRESHNESS_MINUTES` (default 4320), `OFFER_RESPONSE_SECONDS` (900), `OFFER_BATCH_SIZE` (1), `ACCOUNT_HARD_DELETE_ENABLED` (false — §9).

---

## 2. Database migrations (Phases 3–5, 10)
Migrations live in `supabase/migrations/` as numbered `*.up.sql` / `*.down.sql` pairs. They are **additive, reversible, and schema-defensive** (guarded by `IF NOT EXISTS`, `to_regclass`, and column-existence checks) so they will not touch unrelated apps in the shared project (Rule 5).

**Before applying (Rule 7):** take a backup — GitHub → Actions → **Database backup** → *Run workflow* (see `.github/workflows/db-backup.yml`).

Apply in order via the Supabase SQL editor or CLI:
```bash
supabase db push            # if the project is linked with the Supabase CLI
# or paste each 000N_*.up.sql into Supabase → SQL Editor, in numeric order.
```

| Migration | Adds | Fixes |
|---|---|---|
| 0001 | order-integrity columns, `order_events`, `stock_changes`, `create_order_atomic`, `set_order_status` | C1, C2, C4 |
| 0002 | `user_roles`, `admin_audit_log`, `is_admin()`, `assign_role()`, RLS closing public order/payout inserts | C3, C5 |
| 0003 | `notification_outbox`, `admin_devices`, `enqueue_notification`, `claim_notifications`, `resolve_notification` | H3, H4, H5 |
| 0004 | `purchase_requests`, `order_offers`, `matching_config`, `match_eligible_farmers` | Phase 5 |
| 0005 | `create_purchase_request`, `match_and_offer`, `accept_offer`, `reject_offer`, `expire_overdue_offers`, `claim_unmatched_requests` | Phase 5 |
| 0006 | `account_deletion_requests`, `_acct_anonymize`, `request_account_deletion`, `mark_account_purged`, `farmers.deleted_at` | H6 |

> **Verify after applying:** Supabase → Advisors → **Security** should show no new RLS-disabled or policy warnings on AgriBridge tables. Rollback with the matching `.down.sql` if needed.

---

## 3. Super-admin assignment (Phase 3 — Rule 9)
Admin authority is server-controlled (the `user_roles` table), **never** a client-side email check or hidden button. Assign the owner by **verified UUID**, not by email string:

1. Supabase → Authentication → Users → confirm `zealmugumya@gmail.com` exists and **email is confirmed**; copy its UUID.
2. In the SQL editor (service role):
   ```sql
   select public.assign_role('<VERIFIED-UUID>', 'superadmin', null);
   ```
   (See the footer of `supabase/migrations/0002_admin_identity_rls.up.sql`.)
3. Enable **MFA** for that account (Auth → user → require MFA).
4. Optionally add the UUID to `ADMIN_ALLOWED_UUIDS` as a bootstrap fallback, then remove it once the role row exists.

The backend authorizes admins via `require_admin_user()` → `is_admin_uuid()`, which checks `ADMIN_ALLOWED_UUIDS` **and** the `user_roles` table.

---

## 4. Discord operations feed (Phase 7)
1. Discord → Server Settings → Integrations → Webhooks → **New Webhook** → copy the URL.
2. Set `DISCORD_WEBHOOK_URL` in Render. Restart.
3. Verify: place a test order → an `order.created` embed should appear. Delivery has bounded retries, 429 handling, and a **dead-letter fallback** (a failed critical Discord alert is re-enqueued as an admin push so it is never silently lost).
- The URL is never logged or returned to clients.

---

## 5. Notification / matching worker (Phase 5)
The durable queue lives in Postgres (`notification_outbox`), so the worker runs as a **separate process** from the API.
- **Command:** `python -m services.worker`
- **Render:** create a **Background Worker** service with the **same env** as the API. It handles `SIGTERM`/`SIGINT` for graceful shutdown, drains notifications, and runs a periodic matching pass.
- **Tunables:** `WORKER_POLL_SECONDS` (5), `WORKER_BATCH_SIZE` (10), `WORKER_MATCH_EVERY_N_POLLS` (3).
- Without a running worker, enqueued notifications and matching simply wait in the DB (durable) — nothing is lost.

---

## 6. Sentry error monitoring (Phase 8)
1. Sentry → create project (Flask) → copy the DSN.
2. Set `SENTRY_DSN`, and optionally `SENTRY_ENVIRONMENT` / `SENTRY_TRACES_SAMPLE_RATE`.
3. `services/monitoring.py` scrubs request data/headers/cookies before send and drops the event if scrubbing fails. Without a DSN, Sentry is a no-op.
- Correlation IDs (`X-Correlation-ID`) tie a Sentry event to the request's structured logs.

---

## 7. Firebase Cloud Messaging — admin push (Phase 6)
1. Firebase console → create/select project → **Project settings → Service accounts → Generate new private key** (JSON).
2. Provide it to the backend **one** of two ways:
   - `FCM_CREDENTIALS_JSON` = the file's contents (inline), **or**
   - `FCM_SERVICE_ACCOUNT_PATH` = a path on the server to the JSON file.
   - `FCM_PROJECT_ID` optional (defaults to `project_id` in the JSON).
3. Add `cryptography` (already in `requirements.txt`) — PyJWT needs it to sign the RS256 assertion.
4. **Client side:** the admin app obtains an FCM registration token and calls `POST /api/admin/devices {token, platform}` with the admin's Supabase session. The API only registers a token **after** verifying the caller is an admin (Rule 9).
5. Push payloads are **lock-screen safe**: the visible notification is a short generic summary; detail rides in `data` and renders in-app after auth. Dead tokens (UNREGISTERED) are pruned automatically.
- Until credentials are set, push is a safe no-op and critical alerts fall back to Discord/dead-letter.

---

## 8. Payments — Flutterwave (Phase 9)
The existing verified webhook (`/api/pay/webhook/flutterwave`) is preserved: it checks the shared `verif-hash` **and** re-verifies the transaction with Flutterwave server-side before marking an order paid.
1. Flutterwave → Settings → API Keys → Secret Key → `FLW_SECRET_KEY`.
2. Invent a webhook secret → set `FLW_WEBHOOK_HASH` **and** paste the same value in Flutterwave → Settings → Webhooks.
3. Webhook URL: `https://agribridge-1-og7a.onrender.com/api/pay/webhook/flutterwave`.
4. Test in **sandbox** first. With keys empty, online payment stays OFF and checkout uses cash-on-delivery.
- Order totals are recomputed **server-side** by `create_order_atomic`; a client-supplied price is ignored (fixes C1/C4).

---

## 9. Account deletion (Phase 10 / H6)
- Endpoint: `POST /api/account/delete` (identity from the verified session; a body-supplied id is ignored).
- **Default (reversible):** anonymizes the caller's own personal data, drops their push tokens/role, preserves the financial ledger, and records an auditable request. Reversible only from a backup.
- **Irreversible purge:** set `ACCOUNT_HARD_DELETE_ENABLED=true` **only after** (a) a fresh backup, (b) reviewing FK `ON DELETE` behaviour on `orders`/`payouts`/`reviews` so purging `auth.users` cannot cascade-destroy the ledger, and (c) explicit approval (Rule 8). Then deletion also calls the GoTrue admin endpoint.
- **Play Store:** wire a settings-screen "Delete account" button to this endpoint and publish the web deletion URL (see `docs/PLAY_STORE_RELEASE.md` §8).

---

## 10. Health & observability
- `GET /health` → 200 with `supabase_connected: true`, or **503** `degraded` when the DB round-trip fails (M6 — no lying health checks).
- `GET /health/live` → process liveness, independent of dependencies.
- Logs are JSON with correlation IDs; secrets are redacted by `services/logging.py`.
- `GET /api/pay/providers` → which payment methods are live.

---

## 11. Running tests / lint locally
```bash
pip install -r requirements-dev.txt
ruff check app.py services/ tests/     # lint gate (must be clean)
pytest tests/ -q                       # 85 tests
```
CI (`.github/workflows/ci.yml`) runs lint + (non-blocking) mypy + pytest on push/PR. **SQL-migration integration tests and Android builds are not part of CI yet** — they need a live DB / Android SDK and are not faked.
