# AgriBridge 2.0 — Production Audit (Phase 1)

Date: 2026-10-09
Scope: full repository inspection of `agribridge-main` as delivered in `agribridge-main.zip`.
Method: every file read in full. Frontend (`static/index.html`, 6,361 lines) read end-to-end; backend (`app.py`, 1,419 lines), admin console (`static/admin.html`), mobile wrapper, deploy config, and docs read directly.

> **Verification boundary.** The live Supabase database (schema, RLS policies, triggers, functions) and the Render deployment were **not** reachable from this environment — no credentials, no network access to those services. Statements about the database below are inferred from the README, the code that reads/writes it, and the admin console. They are marked **[UNVERIFIED — needs DB access]** where confirmation requires connecting to Supabase. Nothing in this document claims a test, migration, or deployment passed.

---

## 0. Repository inventory

### Files (26 total, no `node_modules`, no `.git`)
```
app.py                                 Flask backend, 1,419 lines
static/index.html                      Entire web app, 6,361 lines (vanilla JS SPA)
static/admin.html                      Admin console, 431 lines (vanilla JS)
static/sw.js                           Service worker — DEAD ASSET (force-unregistered by index.html)
static/manifest.json, robots.txt, sitemap.xml, icon-192/512.png, .python-version
mobile/package.json                    Capacitor 6 shell — copies static/, no React build
mobile/capacitor.config.json           appId com.agribridge.app, webDir www
mobile/scripts/copy-web.mjs            cp static/ -> www/
mobile/assets/icon.png, mobile/README.md, mobile/.gitignore
requirements.txt                       12 pinned Python deps
render.yaml                            Render web service config
.github/workflows/db-backup.yml        Weekly encrypted pg_dump backup
GO-LIVE.md, README.md
legal/{investor-nda,media-consent,privacy-policy,terms-of-use,README}.md
```

### Backend routes (`app.py`)
| Route | Method | Auth | Notes |
|---|---|---|---|
| `/`, `/health` | GET | none | Health — reports `supabase_connected` = "key is set", **not** a real DB ping (app.py:216) |
| `/api/admin/login` | POST | rate-limited 5/300s | Shared-password login → JWT `role:admin` (app.py:227) |
| `/api/admin/verify` | GET | admin JWT | (app.py:239) |
| `/api/admin/stats` | GET | admin JWT | Loads up to 1000 rows × 4 tables per call (app.py:987) |
| `/api/admin/{listings,orders,farmers,animals,deliveries,supplies}` | GET | admin JWT | (app.py:1007–1062) |
| `/api/admin/order/<id>/status` | PATCH | admin JWT | (app.py:1028) |
| `/api/admin/row` | POST/PATCH/DELETE | admin JWT | Generic editor, column-whitelisted (app.py:1087) |
| `/api/ussd` | POST | none | Africa's Talking USSD (app.py:949) |
| `/api/sms/delivery` | POST | none | Logs webhook, returns OK (app.py:978) |
| `/api/prices` | GET | none | (app.py:1122) |
| `/api/crop-doctor` | POST | rate-limited | Gemini proxy (app.py:1130) |
| `/api/ai` | POST | rate-limited | Groq proxy (app.py:1167) |
| `/api/notify-order` | POST | rate-limited | Best-effort farmer SMS (app.py:1212) |
| `/api/sms-receipt` | POST | rate-limited | Best-effort buyer SMS (app.py:1258) |
| `/api/pay/providers` | GET | none | (app.py:1286) |
| `/api/pay/initiate` | POST | rate-limited | Flutterwave/Pesapal/MTN/COD/direct (app.py:1293) |
| `/api/pay/webhook/flutterwave` | POST | verif-hash | Re-verifies with provider before marking paid (app.py:1346) |

### Database tables referenced (README §3 + code)
`farmers`, `listings`, `animal_listings`, `orders`, `deliveries`, `reviews`, `payouts`, `platform_config`, `market_prices`, `price_data`, `price_alerts`, `cart_sessions`, `contact_messages`, `disease_reports`, `supply_orders`, `supplier_products`, `training_videos`, `vet_bookings`, `fraud_flags`, `ussd_sessions`, `community_posts`.
View: `farmer_ratings`. Triggers (per README): `handle_new_user` (auth.users), `sync_delivery_on_order` (orders).
**[UNVERIFIED — needs DB access]** — actual columns, RLS policies, trigger bodies, and whether all these tables exist as documented.

### Environment variables (from code + GO-LIVE.md)
`JWT_SECRET`, `ADMIN_PASSWORD`, `AT_USERNAME`, `AT_API_KEY`, `AT_SHORTCODE`, `AT_SMS_SENDER`, `SUPABASE_URL`, `SUPABASE_KEY` (service-role), `GEMINI_API_KEY`, `GROQ_API_KEY`, `FLW_SECRET_KEY`, `FLW_WEBHOOK_HASH`, `PESAPAL_CONSUMER_KEY`, `PESAPAL_CONSUMER_SECRET`, `MTN_MOMO_SUBSCRIPTION_KEY`, `PUBLIC_BASE_URL`, `PORT`, `FLASK_ENV`. GitHub secrets: `SUPABASE_DB_URL`, `BACKUP_PASSPHRASE`.
**There is no `.env.example` in the repo** (deliverable #11 outstanding).

### Dependencies
Python (requirements.txt): flask 3.1.0, flask-cors 5.0.0, **flask-sqlalchemy 3.1.1, sqlalchemy 2.0.36, bcrypt 4.2.1, python-dotenv 1.0.1, psycopg2-binary 2.9.10** (these five are **imported nowhere in app.py** — dead dependencies), PyJWT 2.10.1, requests 2.32.3, gunicorn 23.0.0, Werkzeug 3.1.3, africastalking 1.2.9.
Mobile: @capacitor/{core,cli,android,ios} ^6.1.2, @capacitor/splash-screen, status-bar, assets.

---

## 1. Findings (severity-ranked)

Severity: **CRITICAL** (blocks production / data-integrity or auth risk) · **HIGH** · **MEDIUM** · **LOW**.

### CRITICAL

**C1 — Order total is computed in the browser and written directly to the DB.**
Evidence: `static/index.html` `placeOrder()` (~lines 2730–2807) and `submitOrder()` (~3542–3564) insert into `orders` with `total_price: item.price * item.qty`, computed client-side, via direct PostgREST insert. The Flask backend never creates orders and never recomputes totals.
Impact: a buyer can insert an order for any price (e.g. `total_price: 1`) directly against Supabase. Financial integrity is client-controlled. Violates mandate Phase 9 ("No payment is declared successful solely because the frontend reports success") and Phase 5.
Remediation: move order creation server-side (Flask or a Supabase RPC/Edge Function) that re-reads the listing price, recomputes totals, and inserts inside a transaction. Deny direct client INSERT on `orders` via RLS.

**C2 — No stock reservation or decrement; overselling is possible.**
Evidence: `placeOrder()`/`submitOrder()` never reduce `listings.quantity_kg`; no hold/reserve logic exists anywhere (index.html §5–6). No server-side atomic stock update.
Impact: two buyers can order the same limited stock; negative/oversold inventory; farmers receive orders they cannot fulfil. Violates mandate Phase 4 ("Do not allow negative stock or overselling") and Phase 5 step 12.
Remediation: atomic `UPDATE ... SET quantity = quantity - :n WHERE id = :id AND quantity >= :n` inside the order transaction (Postgres RPC), with a `stock_reservations`/event table for audit.

**C3 — Administrator identity is a single shared password, with no per-user identity, MFA, or audit trail.**
Evidence: `app.py:44` `ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD','')`; `admin_login()` (app.py:227–237) compares one password and mints a JWT with `sub:'admin'`. `GO-LIVE.md:111` documents the default as `agribridge2026`. There is no link to any Supabase user, no `zealmugumya@gmail.com` binding, no login auditing, no MFA, no session revocation.
Impact: anyone who obtains the shared password (documented in the repo) is a full admin; actions are unattributable. Violates mandate Rule 9 and Phase 3 entirely.
Remediation: replace with Supabase-Auth-backed admin — assign a `role='admin'` (or `is_superadmin`) to the verified UUID for `zealmugumya@gmail.com` via a privileged migration, enforce server-side + RLS, require MFA, and write an admin audit log. See Phase 3 plan.

**C4 — Payment "success" is assumed on the client before any provider verification.**
Evidence: `placeOrder()` mirrors the order into `localStorage['ab_orders']` with `status:'confirmed'` regardless of payment outcome; for COD/bank/MoMo-direct, confirmation is purely optimistic (index.html §5, §8). The Flutterwave webhook (app.py:1346) is correctly verified server-side, but the client order-completion path does not depend on it.
Impact: orders can present as confirmed/paid without money moving. Violates Phase 9.
Remediation: order status transitions must be server-authoritative, driven by the verified webhook; the client should poll/subscribe for the authoritative status, never set `paid`/`confirmed` itself.

**C5 — Frontend writes directly to ~17 tables; RLS is the only authorization layer and is unverified.**
Evidence: `sb()`/`sbInsert()`/`sbUpdate()` in index.html (~1939–2006) hit `/rest/v1/<table>` from the browser; the publishable key is used as the bearer when no user token is present. README §4 asserts owner-scoped RLS on all tables.
Impact: if any policy is missing or permissive, an anonymous browser can insert/update/delete marketplace, order, payout, or profile data. The exposed key is a **publishable** key (`sb_publishable_...`), so this is **not** a service-role leak — but it makes RLS correctness load-bearing and it is currently unverified.
Remediation: audit every policy against the real DB (Phase 3), close public INSERT on `orders`/`payouts`/`farmers`, and re-test cross-user isolation. **[UNVERIFIED — needs DB access]**

### HIGH

**H1 — Per-boot random JWT secret breaks admin sessions and is incompatible with 2 gunicorn workers.**
Evidence: `app.py:37–42` — if `JWT_SECRET` is unset it generates `secrets.token_hex(32)` per process. `render.yaml:7` starts gunicorn with `--workers 2`.
Impact: with 2 workers, each has a *different* secret; a token minted by worker A is rejected by worker B → intermittent 401s. Any restart invalidates all admin sessions. (Fails closed, so not a security hole, but a reliability defect.)
Remediation: require `JWT_SECRET` and refuse to boot (or serve 503 on admin routes) if unset; never derive per-worker secrets.

**H2 — In-memory state is not multi-worker safe.**
Evidence: `USSD_SESSIONS = {}` (app.py:253) and the rate-limit buckets `_rl_buckets` (app.py:74) are process-local; `--workers 2`.
Impact: multi-step USSD flows (list produce, AI doctor) break when consecutive requests land on different workers; rate limits are effectively doubled and per-worker. Violates Phase 10 (durable state) and Phase 5 (no in-memory tasks).
Remediation: move USSD session + rate-limit state to Redis or Postgres; or pin to 1 worker as a stopgap (documented).

**H3 — No durable job queue / worker for order routing, notifications, or matching.**
Evidence: none exists. `notify-order` is a synchronous best-effort SMS (app.py:1212). No farmer-matching, no offer/timeout/retry, no notification outbox.
Impact: mandate Phases 5, 6, 7 are entirely unimplemented. Orders are not routed to farmers; the admin is not alerted; there is no retry/idempotency for async work.
Remediation: introduce a durable queue (e.g. Supabase `pgmq`/`outbox` table + worker, or a Render background worker) with idempotency keys, bounded retries, and a dead-letter path.

**H4 — No push notifications of any kind; service worker is force-unregistered.**
Evidence: index.html (~4111–4129) unregisters all service workers and clears caches; `sw.js` is dead. No Web Push, no FCM, no `@capacitor/push-notifications`.
Impact: mandate Phase 6 (admin phone alerts) is unimplemented. Critical events have no real-time delivery path.
Remediation: add Capacitor Push + FCM with a server-side token registry and notification outbox.

**H5 — No Discord, Sentry, structured logging, correlation IDs, or metrics.**
Evidence: backend logs via `print(...)` only (e.g. app.py:127, 1342); no request IDs, no error-monitoring integration, no health check that touches the DB.
Impact: mandate Phases 7 and 8 unimplemented; outages are invisible.
Remediation: structured JSON logging + correlation IDs, Sentry with secret scrubbing, a real `/health` that pings Postgres, and a Discord outbox with dedup/backoff/dead-letter.

**H6 — No account-deletion or data-export flow (Google Play + privacy-policy gap).**
Evidence: index.html has static Terms/Privacy (~5787–5827) promising "Account data deleted within 30 days of account closure request," but there is **no in-app deletion button or request path**; only `privacy@agribridge.ug`. `logOut()` just signs out.
Impact: violates mandate Phase 10 (privacy/account deletion) and is a **Google Play account-deletion requirement** for apps that allow account creation — blocks Play submission.
Remediation: implement self-service account deletion (anonymize/delete `farmers` row + owned data via a server RPC) and a data-export path.

**H7 — No automated tests and no CI (beyond the backup workflow).**
Evidence: repo has `.github/workflows/db-backup.yml` only; no test files, no lint/type/build workflow.
Impact: mandate Phase 11 unimplemented; regressions are undetectable; "production-ready" cannot be substantiated.
Remediation: add pytest (backend), Vitest/RTL (React once built), RLS isolation tests, and a CI workflow (lint + typecheck + unit + build).

### MEDIUM

**M1 — Mobile app is a static copy of the web SPA, not a React build; Capacitor 6 will not meet the Play API-36 target.**
Evidence: `mobile/scripts/copy-web.mjs` copies `static/` → `www/`; `mobile/package.json` uses Capacitor ^6.1.2. Capacitor 6 targets Android SDK 34; the mandate requires **API 36** for new releases from 2026-08-31.
Impact: mandate Phase 2 unimplemented; the app cannot be submitted at the required target SDK without upgrading Capacitor (7+) and verifying the Gradle/AGP toolchain.
Remediation: build a real React+TS app, integrate Capacitor 7 (verify API 36 / compileSdk), keep appId `com.agribridge.app`, produce an `.aab` workflow. **[Version compatibility must be checked against Capacitor release notes, not assumed.]**

**M2 — Client-side-only "password" gate on the pitch deck.**
Evidence: `const PITCH_HASH='bbfdac999f...e36c'` (index.html ~3921), SHA-256 checked in-browser, flag in `sessionStorage['ab_pitch_auth']`.
Impact: trivially bypassable; protects no server resource. Illustrates the pattern of client-side controls the mandate forbids (Rule 9).
Remediation: remove or move behind real server-side authz.

**M3 — XSS surface: `innerHTML` string concatenation with inconsistent escaping.**
Evidence: views built via `innerHTML` throughout; `escHtml()`/`escapeHtml()` exist but are applied unevenly; user-generated fields (reviews, community posts, listing titles, notes) are stored by other users directly to the DB.
Impact: stored XSS on a shared origin where PII lives in `localStorage` → session/data theft.
Remediation: React (auto-escaping) removes most of this; until then, escape every interpolation and add a strict CSP.

**M4 — Mock/sample data rendered as if real in production paths.**
Evidence: `SAMPLE_ANIMALS`, `sampleListings`, `samplePrices`, `sampleTraining`, `B2B_PRODUCTS`, cold-chain facilities/trucks, fake notification items, hardcoded home price ticker (index.html ~1030–1044, 2287, 4072–4075, 4188–4199, 4504, 5941–5943).
Impact: violates mandate "No mock data in production paths" and "no fake success messages."
Remediation: remove or clearly gate behind an offline/empty state.

**M5 — Many "success" toasts with no persistence (fake success).**
Evidence: `submitFinanceRequest`, `submitDispute`, `buyInsurance`, `createSACCO`, `bookStorage`, `bookTransport`, `applyPhyto`, `checkOrigin`, `startTrace`, `getWeatherSMS`, `submitB2BOrder`, `generateInvoice`, `setupStandingOrder`, `submitB2BApplication`, `submitRefund`, `submitRating`, `requestPartnership` — all showToast-only or fully client-side (index.html §10.11).
Impact: users believe actions succeeded when nothing was recorded. Violates mandate Rules.
Remediation: implement or remove; never show success without a persisted result.

**M6 — CORS allows `localhost` in production; health check does not verify the DB.**
Evidence: `app.py:29–33` allows `http://localhost:3000` / `127.0.0.1:3000`; `/health` returns `supabase_connected: bool(SUPABASE_KEY)` (app.py:216) — a key-presence check, not connectivity.
Impact: mandate Phase 10 explicitly warns against treating a green health endpoint as proof. Localhost origin widens the CSRF/CORS surface.
Remediation: drop localhost origins in prod; make `/health` execute a real trivial query and report dependency status.

**M7 — Hardcoded financial/PII details in the frontend.**
Evidence: `DFCU Bank, A/C 01234567`, MoMo `0755966690`, phone `+256 755 966 690`, DPO name "Zeal Mugumya" (index.html ~5736, 5822).
Impact: brittle, and a bank account number in a public bundle. Some (support phone) is intentional; the bank A/C should move to config/CMS.
Remediation: externalize to `platform_config`/env-driven content.

### LOW

- **L1 — Dead dependencies** (flask-sqlalchemy, sqlalchemy, bcrypt, python-dotenv, psycopg2-binary) in requirements.txt; unused in app.py. Trim or actually use Postgres directly for atomic operations (recommended for C1/C2).
- **L2 — Dead asset** `static/sw.js` never registered.
- **L3 — Password-policy mismatch**: register UI says "min 8", code enforces `length < 6` (index.html ~5240).
- **L4 — Hardcoded USSD test phone** `+256700000000` in the simulator call (index.html ~3371).
- **L5 — `admin_stats` scans up to 1000 rows/table per request** (app.py:987) — no pagination/indexes; will not scale (Phase 10).
- **L6 — No security headers beyond basics**: `X-XSS-Protection` (deprecated), no `Content-Security-Policy`, no `Strict-Transport-Security` on API responses (app.py:98–104).
- **L7 — Client-side CSRF token / login lockout** in sessionStorage/localStorage are bypassable theater (index.html §10.9).
- **L8 — No `.env.example`, no `docs/` directory** prior to this audit.

---

## 2. What already works and must be preserved

These are genuinely reasonable and should not be broken by the rewrite (mandate Rule 1):

- **Flutterwave webhook verification** (app.py:1346–1371): checks `verif-hash` with `hmac.compare_digest` **and** re-verifies the transaction with the provider before marking paid; idempotent by `payment_ref`. This is the correct pattern — the gap is that the *client* order path doesn't depend on it (C4).
- **Admin JWT with constant-time password compare** (app.py:234) and fail-closed behavior when `ADMIN_PASSWORD`/`JWT_SECRET` are unset (app.py:37–44). Good instinct; the problem is the shared-secret *model* (C3), not the crypto.
- **Column-whitelisted generic admin editor** (`_ADMIN_COLS`, app.py:1066–1085) prevents the admin token from writing protected columns (id, timestamps, ownership). Keep this pattern.
- **Payment providers are OFF until keys are set** (app.py:1276–1283) — safe pluggable design.
- **SMS/AI are best-effort no-ops when unconfigured** — never block the order flow (app.py:1212–1269, 1174–1177).
- **Weekly encrypted DB backup workflow** (`.github/workflows/db-backup.yml`) with documented restore — a real recovery primitive.
- **USSD menu system** (app.py:277–946) is complete and coherent ( modulo H2 worker-state bug).
- **Publishable key exposure is by-design**, not a leak (README §4). Do not "fix" this by hiding the key; fix it by verifying RLS (C5).

---

## 3. Baseline build/test result

- **Backend:** not executed in this environment. No test suite exists. `requirements.txt` pins look internally consistent (Flask 3.1 / Werkzeug 3.1.3 / PyJWT 2.10). Five deps are unused (L1). **Baseline: no tests to run — this is itself finding H7.**
- **Frontend:** static HTML/JS, no build step; nothing to compile. Cannot be type-checked or unit-tested as-is.
- **Mobile:** Capacitor 6 static-copy; `npm install` not run here; no Android SDK/Gradle present, so no `.aab` can be produced or verified in this environment.

---

## 4. Remediation priority (maps to mandate phases)

1. **Server-authoritative orders + atomic stock** (C1, C2, C4) → Phase 5, 9.
2. **Real admin identity for `zealmugumya@gmail.com` + RLS verification + MFA + audit** (C3, C5) → Phase 3.
3. **Durable queue/worker + notification outbox** (H3) → Phase 5.
4. **Admin push notifications (FCM)** (H4) → Phase 6.
5. **Discord ops feed + Sentry + structured logging + real health check** (H5, M6) → Phase 7, 8.
6. **Account deletion / data export** (H6) → Phase 10, Play requirement.
7. **React + TS app, Capacitor 7 / API 36, `.aab`** (M1) → Phase 2.
8. **Tests + CI** (H7) → Phase 11.
9. Frontend hygiene: XSS, mock data, fake success (M3–M5) → largely resolved by the React rewrite.

See `docs/IMPLEMENTATION_STATUS.md` for sequencing, what is locally verifiable, and what is blocked on external credentials.
