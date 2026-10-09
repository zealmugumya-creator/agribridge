# AgriBridge 2.0 — Implementation Status

Last updated: 2026-10-09 (backend integrity & security track substantially complete)

This file is the source of truth for progress across the 12-phase mandate. It records what is **done and verified locally**, what is **implemented but needs live credentials to verify**, and what is **blocked on external access/decisions only the owner (Zeal) can provide**.

> **Verification boundary (Rule 3 / Rule 11):** The live Supabase database, Render, Firebase, Discord, Sentry, and Flutterwave were **not reachable** from this environment. All Python is covered by an offline test suite (85 tests, all passing) and ruff lint is clean. SQL migrations are written additive/reversible/schema-defensive and are **documented as requiring review against the real schema before applying** — no migration or deployment is claimed to have been run.

---

## Legend
- ✅ **Verified** — completed and confirmed here (code compiles, tests pass, lint clean).
- 🟡 **Implemented, unverified** — code/config written; live confirmation needs external access (Supabase, Render, Firebase, Discord, Sentry, Flutterwave, Play Console).
- ⛔ **Blocked** — cannot proceed without credentials/decisions only the owner can provide.
- ⬜ **Not started**.

---

## Phase status

| Phase | Description | Status | Notes |
|---|---|---|---|
| 1 | Baseline audit | ✅ | `docs/PRODUCTION_AUDIT.md` from full repo inspection, severity-ranked with `file:line` evidence. |
| 2 | React + TS app & Android/Capacitor API 36 | ⬜ | Largest work item; not started (see open questions). Play-release steps documented in `docs/PLAY_STORE_RELEASE.md`. |
| 3 | Supabase security & admin identity | 🟡 | `user_roles` + `is_admin()` + RLS closing public order/payout inserts (migration 0002); server-side identity via GoTrue in `app.py`. ⛔ Super-admin UUID assignment for `zealmugumya@gmail.com` must be run manually against the real `auth.users` (Rule 9 — never invented). |
| 4 | Farmer stock & verification | 🟡 | Atomic reservation columns + `create_order_atomic` (no oversell) and `stock_changes` audit in migration 0001. Needs live DB to verify. |
| 5 | Automated order routing (durable queue) | 🟡 | `notification_outbox` (0003), farmer matching + ranked offers with deadlines + atomic accept (0004/0005), `services/worker.py` durable loop. Purchase-requests are distinct from paid orders. Needs live DB. |
| 6 | Admin push notifications (FCM) | 🟡 | `services/push.py` (HTTP v1 sender + `admin_devices` registry), device register/unregister endpoints, lock-screen-safe payloads. ⛔ Needs Firebase service account; safe no-op until set. |
| 7 | Discord ops integration | 🟡 | `services/discord.py` — env-only webhook, retries/backoff, 429 handling, dead-letter fallback. ⛔ Needs `DISCORD_WEBHOOK_URL`. |
| 8 | Monitoring & incident response | 🟡 | `services/logging.py` (JSON + correlation IDs + secret redaction), `services/monitoring.py` (Sentry w/ PII scrubbing), real `/health` + `/health/live`. ⛔ Needs `SENTRY_DSN`. |
| 9 | Payments & financial integrity | 🟡 | Existing verified webhook preserved; new server-side order API removes client-trust of totals (C1/C4). ⛔ End-to-end payment verification needs Flutterwave sandbox keys. |
| 10 | Scalability, privacy, reliability | 🟡 | Account deletion / erasure (migration 0006 + `services/account.py` + `POST /api/account/delete` + settings-modal UI in `static/index.html`), indexes in migrations, secure headers + rate limits in `app.py`. Hard auth-purge gated behind `ACCOUNT_HARD_DELETE_ENABLED` (Rule 8). |
| 11 | Testing & CI/CD | ✅ | 85 pytest tests passing, ruff clean, `.github/workflows/ci.yml` (lint + typecheck + test). SQL-integration tests remain ⛔ (need live DB) and are **not faked**. |
| 12 | Admin command center | ⬜ | Depends on the React decision (Phase 2). Backend admin authz primitives are in place. |

---

## Completed & verified locally (✅)
- **Phase 1 audit** — `docs/PRODUCTION_AUDIT.md` (C1–C5, H1–H7, M1–M7, L1–L8 with evidence + remediation; "what works and must be preserved").
- **Config hardening (H1, M6)** — `services/config.py` fails **closed** in production if `JWT_SECRET`/`SUPABASE_KEY` are missing; no localhost CORS in production.
- **Structured logging + correlation IDs (H5)** — `services/logging.py`; JSON logs with secret redaction; request-scoped correlation IDs surfaced via `X-Correlation-ID`.
- **Real health probe (M6)** — `/health` does an actual DB round-trip (200/503); `/health/live` for liveness.
- **Secure headers + rate limits (Phase 10)** — `@app.after_request` security headers; `@rate_limit` on sensitive routes.
- **Test suite + CI (H7)** — 85 tests across orders, matching, notifications, discord, push, account, config/logging, and the API surface; `.github/workflows/ci.yml`.
- **Lint/type gates** — ruff clean; mypy configured non-blocking in CI.

## Implemented, pending live verification (🟡)
- **Server-authoritative orders (C1/C2/C4)** — `services/orders.py` + `create_order_atomic` RPC (price recomputed server-side, stock reserved atomically, idempotent). `POST /api/orders`, `POST /api/orders/<id>/status` (state machine).
- **Admin identity + RLS (C3/C5)** — migration 0002; `verify_supabase_user()`, `is_admin_uuid()`, `require_admin_user()`.
- **Durable queue + worker (H3)** — migration 0003 + `services/worker.py` (`FOR UPDATE SKIP LOCKED` claim, backoff, dead-letter).
- **Farmer matching + offer routing (Phase 5)** — migrations 0004/0005 + `services/matching.py`; `POST /api/requests`, `/api/offers/<id>/accept|reject`, admin-only `/api/worker/match`.
- **FCM admin push (Phase 6)** — `services/push.py`; `POST|DELETE /api/admin/devices`.
- **Discord ops (Phase 7)** — `services/discord.py`.
- **Sentry monitoring (Phase 8)** — `services/monitoring.py`.
- **Account deletion (H6)** — migration 0006 + `services/account.py`; `POST /api/account/delete`.

## Changed / added files
- **New services:** `services/{account,config,discord,logging,matching,monitoring,notifications,orders,push,supabase_client,worker}.py`
- **New migrations:** `supabase/migrations/0001..0006` (each with `.up.sql` + `.down.sql`)
- **New tests:** `tests/{conftest,test_account,test_api,test_config_logging,test_discord,test_matching,test_notifications,test_orders_service,test_push}.py`
- **New config/CI:** `.env.example`, `requirements-dev.txt`, `pytest.ini`, `ruff.toml`, `.github/workflows/ci.yml`
- **Modified:** `app.py` (wiring + new routes), `requirements.txt` (added `sentry-sdk[flask]`, `cryptography`), `static/index.html` (account-deletion UI in settings)
- **Docs:** `docs/PRODUCTION_AUDIT.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/PLAY_STORE_RELEASE.md`, `docs/OPERATIONS_SETUP.md`, `docs/RELEASE_READINESS_REPORT.md`, updated `README.md`

---

## Outstanding blockers (external — owner action required)

1. **Supabase access** — to verify/repair RLS on the ~17 browser-writable tables (C5), inspect the real schema/triggers, and apply migrations 0001–0006. Needed: DB connection string or the ability to run migrations in the dashboard/CLI.
2. **The verified UUID for `zealmugumya@gmail.com`** — must be read from `auth.users` after confirming the account is email-verified, then `select public.assign_role('<UUID>','superadmin',null)` (migration 0002 footer). Never invented (Rule 9).
3. **Firebase service account** — `FCM_CREDENTIALS_JSON` (or path) + project id for admin push (Phase 6).
4. **`DISCORD_WEBHOOK_URL`** — ops feed (Phase 7).
5. **`SENTRY_DSN`** — error monitoring (Phase 8).
6. **Flutterwave sandbox/live keys** — end-to-end payment verification (Phase 9).
7. **Worker hosting decision** — run `python -m services.worker` as a Render background service with the same env as the API (recommended; already coded).
8. **Google Play Console** — app signing, data-safety form, content rating, testing track (Phase 2; see `docs/PLAY_STORE_RELEASE.md`).

---

## Exact next steps

**Track A — locally verifiable now (no external creds):**
1. ⬜ **Phase 2 React + TypeScript scaffold** — replace the 6,361-line vanilla SPA incrementally, screen-by-screen, preserving working features (Rule 1/2). Point the web layer at the new server-side APIs (`/api/orders`, `/api/requests`, `/api/offers/*`, `/api/account/delete`) instead of direct browser table writes.
2. ⬜ **Capacitor 7 / Android API 36** mobile shell + `.aab` build workflow (`docs/PLAY_STORE_RELEASE.md`).
3. ⬜ **Phase 12 admin command center** on top of the Phase 3 authz primitives.
4. ⬜ **SQL integration tests** (pgTAP or a spun-up Postgres) — currently ⛔ without a live DB.

**Track B — needs decisions/creds:**
5. Apply migrations 0001–0006 to Supabase (after backup) and verify RLS with the Advisors security report.
6. Assign the super-admin UUID; enable MFA.
7. Wire Firebase/Discord/Sentry/Flutterwave credentials; verify each channel live.

---

## Open architectural questions (need owner input)
- **Q1 — Frontend strategy:** full React+TS rewrite of all ~20 screens (Phase 2) vs. a phased rewrite that first migrates the critical paths (orders/stock/auth/account-deletion) and moves screens incrementally? A big-bang rewrite is the single largest risk to Rule 1/2 (don't break working features).
- **Q2 — Worker hosting:** Render background worker (recommended, already coded) vs. Supabase scheduled Edge Functions + `pgmq`.
- **Q3 — Hard account deletion:** keep the default reversible anonymization, or enable `ACCOUNT_HARD_DELETE_ENABLED` (irreversible) once FK cascade behaviour on `orders`/`payouts`/`reviews` is reviewed against the real schema?
