# AgriBridge — Test Results

**Date:** 2026-10-22
**Stage:** Bank Transfer (admin-verified), duplicate-signup-alert fix, Pesapal API 3.0 payments, recurring charges, and Google sign-in
**Environment:** Windows, repository `C:\Users\HP\Documents\agribridge`

Every result below was produced by a command run in this session. No live
payment transaction was made and no real Google OAuth consent screen was
exercised. Migration 0017, 0016, 0002 and **0015** are applied live (verified by
read-only PostgREST probes on 2026-10-22; `orders.source_ref` returns HTTP 200).
Push `9123b38` (Pesapal + Google) and push `6077883` (Bank Transfer + signup
fix) both deployed the **API on Render** — live `/api/pay/providers` now returns
`bank:true`. The **Cloudflare SPA auto-deploy silently skipped** (the `Deploy
public web app` run for `6077883` was green but its "Deploy Worker static
assets" step was skipped because `CLOUDFLARE_API_TOKEN` is not in GitHub Actions
secrets), so the SPA was published manually with `wrangler deploy`. Verified
live 2026-10-22: `agribrige.com/` serves the `pm-bank` checkout card and
`agribrige.com/admin` serves the `Bank paid` / `verifyBankTransfer` button. The
bank option is therefore now visible to users. Adding the Cloudflare token to
GitHub secrets is still required so future `static/` pushes publish on their own.
See §6 for exact evidence. Livestock/supplier checkout runs on the category-aware
`create_order_atomic`.

---

## 1. Python unit and API tests

```bash
PYTHONPATH=.test-deps /c/Users/HP/OneDrive/Python314/python.exe -m pytest -q
```

```
436 passed, 103 warnings in 39.64s
```

This includes the 22 Playwright browser tests, the 14 Bank Transfer /
signup-alert tests (`tests/test_bank_transfer.py`) and the 5 deploy-workflow
contract tests (`tests/test_deploy_workflow.py`). Run with the system `python`
(3.12) the Playwright E2E module skips because `.test-deps` is built for Python
3.14 and its native `greenlet` extension cannot load there. The skip is a local
interpreter mismatch, not a product failure; the 3.14 run is the full result.

Targeted payment/Pesapal run:

```bash
PYTHONPATH=.test-deps /c/Users/HP/OneDrive/Python314/python.exe -m pytest \
  tests/test_ledger.py tests/test_payments.py tests/test_card_payments.py \
  tests/test_admin_finance.py tests/test_settlements.py tests/test_pesapal.py -q
```

```
117 passed, 69 warnings in 2.96s
```

Targeted browser + Pesapal slice:

```bash
PYTHONPATH=.test-deps /c/Users/HP/OneDrive/Python314/python.exe -m pytest \
  tests/e2e/test_consent_ui.py tests/test_pesapal.py tests/test_pesapal_ui.py -q
```

```
53 passed in 59.23s
```

(22 browser tests + 19 Pesapal unit tests + 12 static UI tests.)

Targeted Bank Transfer + signup-alert slice:

```bash
PYTHONPATH=.test-deps /c/Users/HP/OneDrive/Python314/python.exe -m pytest \
  tests/test_bank_transfer.py -q
```

```
14 passed, 12 warnings in 0.67s
```

Covered: bank is always offered offline; `/api/pay/providers` returns
`bank_accounts` only from `BANK_TRANSFER_ACCOUNTS` and never a hardcoded number;
a source scan asserts no fabricated account (e.g. `9030`/Stanbic) appears in
`app.py`/`index.html`; `initiate` returns offline `mode:'bank'`; the verify
endpoint requires an admin token (401 without one), rejects a short reference
(400), a non-bank order (409), and a cancelled order (409); on success it marks
paid using the stored order total (asserts `provider='bank'`, the reference, and
that the posted `received` equals the DB amount, not a client value), reports
`already_paid` on replay, and returns 503 + a `bank_verify_failed` system_event
when the ledger post fails. The signup block asserts the `#signups` alert fires
once for a genuinely new account, is suppressed when the `signup_alerted`
system_event marker already exists, and is suppressed for an old account.

## 2. Lint

```bash
PYTHONPATH=.test-deps python -m ruff check app.py services/ scripts/ tests/
/tmp/ruff084/bin/ruff.exe check app.py services/ scripts/ tests/
```

Both the local ruff 0.17.1 and the CI-pinned 0.8.4 report:

```
All checks passed!
```

`node --check` parses all inline script blocks in `static/index.html` (4) and
`static/admin.html` (1).

## 3. SQL migration suites (real Postgres via PGlite)

```bash
cd tests/sql && npm test
```

| Suite | Assertions | Result |
|---|---|---|
| `legal_consent.test.mjs` | 31 | all passed |
| `ledger.test.mjs` | 36 | all passed |
| `security_lockdown.test.mjs` | 17 | all passed |
| `category_orders.test.mjs` | 10 | all passed |
| `settlements.test.mjs` | 11 | all passed |
| `pesapal.test.mjs` | 24 | all passed |

The Pesapal suite applies migration 0017 twice, verifies every payment method
the checkout offers is accepted (including `pesapal`), NULL stays accepted and
unknown methods stay rejected, and the constraint is added `NOT VALID` so
legacy rows are not rescanned. It proves `anon` and `authenticated` cannot read
`payment_gateway_state`, `payment_subscriptions` or `subscription_charges`;
gateway state is one row per provider; correlation ids and tracking ids are
unique; statuses are constrained; replayed IPN cycles cannot double-charge; and
deleting a subscription keeps its charge history. The down migration removes
the tables, restores the legacy constraint, keeps existing Pesapal orders but
refuses new ones, and the migration can be re-applied.

The existing suites continue to cover ledger integrity, the original
anonymous-read vulnerability in 0014, category-aware pricing in 0015, and
settlement guards in 0016.

## 4. New coverage in this stage

`tests/test_pesapal.py` (19 tests):

* Sandbox is the default; `live`/`production` selects the live host.
* `RequestToken` is cached and sent as a Bearer token; secrets are not in the
  test's captured headers.
* Unsafe payment redirects, invalid tracking ids and non-HTTPS IPN URLs are
  rejected.
* The provider flag requires **both** consumer credentials.
* `POST /api/pay/initiate` uses the server-computed order total, sends a stable
  `account_number`, a unique attempt id, the registered notification id and the
  callback URL; a foreign order is refused and an unknown order is a 404.
* A callback is paid only when `GetTransactionStatus` reports completed and
  echoes the checkout reference; underpayment, failure, missing currency and a
  mismatched reference change nothing.
* The webhook acknowledges with the JSON shape Pesapal expects and returns an
  unsuccessful status when verification is unavailable.
* Recurring charges post `recurring:<tracking_id>` to
  `PROVIDER_CLEARING` / `SUSPENSE_OVERPAYMENT`, never reopen the original order,
  and are idempotent on replay.
* Zero/negative/non-numeric recurring amounts and non-UGX currencies are
  rejected.

`tests/test_pesapal_ui.py` (12 static checks, shared by the Pesapal and Google
slices):

* Exactly one login submit id and no stale duplicate.
* Google buttons exist, start hidden, and are gated by
  `/auth/v1/settings`; OAuth uses `provider: google`, the current origin and
  `prompt=select_account`.
* The OAuth onboarding modal collects name/phone/role/district/terms, cannot be
  dismissed with Escape or a backdrop click, and syncs profile + legal data.
* Pesapal appears in the payment selector only when enabled, shows its panel,
  starts the hosted payment, and handles paid/pending/failed/recurring returns.
* Admin Finance wires the Recurring view to `/api/admin/finance/recurring`.
* Browser assets contain no `service_role`, `sb_secret_`, Supabase service key,
  Pesapal/Flutterwave secret, `PESAPAL_IPN_ID` or OAuth `client_secret`;
  `.env.example` documents variable names only.

`tests/test_admin_finance.py` (2 new tests):

* An unapplied 0017 returns `503 recurring_not_initialized` and names
  `0017_pesapal_payments` in `fix`.
* The recurring summary counts only paid charges, totals only paid amounts, and
  reports paid charges without a ledger reference as
  `paid_amount_without_ledger`.

`tests/e2e/test_consent_ui.py` (22 Playwright tests, 3 new):

* `test_google_option_is_offered_on_login_and_registration` — provider settings,
  button visibility, exact OAuth arguments, redirect URL and zero page errors.
* `test_google_onboarding_blocks_until_terms_and_saves_the_profile` — the modal
  survives Escape; phone validation; terms required; saving writes role, a
  `+256...` phone, `legal_signup` versions and three legal acceptances; the
  legal review modal does not appear; zero page errors.
* `test_pesapal_is_offered_only_when_the_server_enables_it` — hidden while
  disabled; after enabling credentials the API reports `pesapal: true`, the
  option appears, selecting it shows the hosted-checkout panel and hides card,
  the explanation mentions Visa and recurring, and cash hides it again.

The 19 pre-existing browser tests still pass (legal consent, signup validation,
order flows and checkout UI).

### Buyer-UI redesign repair set (push `db9d92f`, 2026-10-22)

The redesign at `74879f8`/`20f45f2` arrived with **21 failures and a broken lint
gate**. Reproduced locally, fixed, and each fixed item is pinned by a test:

| Failure | Before | After |
|---|---|---|
| `tests/test_business_config.py` (2) — banned `support@agribridge.ug` back in `app.py` | FAIL | PASS (`VAPID_CONTACT` now defaults to `business.SUPPORT_EMAIL`) |
| `tests/e2e/test_consent_ui.py::test_pesapal_is_offered_only_when_the_server_enables_it` — `syncPayMethods()` read an unfetched cache | FAIL (tile stayed hidden although the API enabled it) | PASS (awaits `getEnabledProviders()`), verified against **live** production in the browser (audit §17) |
| `tests/e2e/test_consent_ui.py` (15) — first-visit splash overlay intercepted every click | FAIL | PASS (fixture enters the app as a returning user; no assertion weakened) |
| `tests/test_pesapal_ui.py:109` — tile-order literal | FAIL | PASS (assertion now order-independent, still requires all six methods) |
| `ruff check app.py services/ scripts/ tests/` (pinned 0.8.4) — I001 + 2x E701 in the new `_send_web_push` | FAIL | **All checks passed** |
| `tests/test_deploy_workflow.py` (2 new) — deploy failure reason invisible without log access | FAIL against pre-fix workflow | PASS (`::error::wrangler deploy failed`, output capture, `exit "$status"`) |

Full-suite result after the repairs: **436 passed**; SQL suites **129 assertions**
pass unchanged (no SQL touched); `python scripts/build_legal.py --check` →
"legal outputs up to date". CI on the pushed commit: `Backend lint + type + tests`
**success** and `Migration 0007 on real PostgreSQL` **success** (run `39785941491`).

## 5. What these tests do not prove

* Pesapal API calls are faked. Token caching, redirect validation, verification
  and recurring posting are server-logic tests, not a gateway integration test.
* Google OAuth is stubbed in the browser. No Google client, consent screen or
  real identity provider was contacted.
* The admin Recurring view was exercised with fake Supabase reads, not against
  a database with 0017 applied.
* No money moved, no provider webhook arrived from the internet, and no
  production database row was written.

## 6. Live verification status

| Item | Evidence | Status |
|---|---|---|
| Migration 0013 (ledger core) | Supabase SQL Editor success; SQL suite passes | Applied by owner, verified |
| Migration 0014 (internal-table lockdown) | Supabase SQL Editor success; anon `GET` on `system_events`/`transporters` returns `401` | Applied, verified |
| Migration 0015 (category pricing) | Read-only probe 2026-10-22: `orders.source_ref` select returns `200 []` (column exists) | **Applied live**; category checkout not exercised with a real order |
| Migration 0002 (admin identity RLS) | Read-only probe: `user_roles` exists (anon gets `200 []` through RLS) | Applied live; policy behavior tested in PGlite |
| Migration 0016 (settlements) | Read-only probe: `payouts.commission_amount`/`net_amount` select succeeds | Applied live; settle flow not exercised live |
| Migration 0017 (Pesapal) | Live tables exist; anon reads return `401` / `42501` | Applied live, RLS verified by probe |
| Admin Finance read endpoints | Live admin panel displays the Finance tab | Verified earlier |
| Payment → ledger posting | Unit/API tests with faked providers only | **Not yet exercised live** |
| COD → ledger posting | Unit/API tests with faked RPC only | **Not yet exercised live** |
| Payout → settlement journal | Unit/API tests and PGlite only | **Not yet exercised live** |
| Pesapal checkout / callback / IPN | Faked transport only; no credentials | **Not yet exercised live** |
| Pesapal recurring charge | Faked transport + PGlite | **Not yet exercised live** |
| Google sign-in | Stubbed OAuth in Playwright | **Not yet exercised live** |
| Browser E2E / Playwright | 22 passed locally (Chromium) | Verified in test harness |
| Pesapal UI / Google UI in a real browser against live services | Served locally with stubs | **Not yet exercised live** |
| API deployment of this stage | Push `9123b38`; live `/health` returns `200` with `supabase_connected:true`; `/api/pay/providers` includes `pesapal`; Pesapal callback route returns `302`; admin recurring route returns `401` | **Deployed and reachable**; business flows unverified |
| Public SPA bundle (Pesapal/Google) | Cloudflare run `39710797408` succeeded; live HTML contains `pm-pesapal` and `googleLoginBtn` | **Deployed**; live flows unverified |
| Bank Transfer method (API — Render) | Push `6077883`; live `GET /api/pay/providers` returns `bank:true` and `bank_accounts:[]` (empty until `BANK_TRANSFER_ACCOUNTS` is set in Render); `/health` `200 supabase_connected:true` | **DEPLOYED and verified live** |
| Bank Transfer checkout card + admin button (Cloudflare SPA) | Auto-deploy workflow for `6077883` **skipped** (missing `CLOUDFLARE_API_TOKEN`), so the SPA was published manually with `wrangler deploy` (version `15ff5212-5542-41f5-9682-01cf94903f8b`, account `Zealmugumya@gmail.com`). Live `GET https://agribrige.com/` now contains `pm-bank`/`renderBankOptions`/`bankInfoPanel`; `GET https://agribrige.com/admin` contains `verifyBankTransfer`/`Bank paid` | **DEPLOYED and verified live** on both the custom domain and `agribridge.zealmugumya.workers.dev`; checkout not exercised with a real bank order |
| CI on the repaired `main` | Push `db9d92f`: `Backend lint + type + tests` **success** and `Migration 0007 on real PostgreSQL` **success** (run `39785941491`); previously **failure** on `20f45f2` | **Green in GitHub**, matching the local 436-test result |
| `Deploy public web app` with the owner's new secrets | Run `39785941174` job `119849040101` **failure**, annotation: `Authentication error [code: 10000]` + `Invalid access token [code: 9109]` | **VERIFIED BLOCKER — stored token value rejected by Cloudflare**; nothing published by CI |
| Redesigned SPA + checkout fix | Published from an authenticated workstation: version `ffecfbb1-7937-4d90-8f97-2d610004a934`. Live HTML is sha256-identical to `static/index.html` at `db9d92f`; production browser check shows `window._payProviders` empty on load, then populated by `syncPayMethods()` → Bank tile visible, Pesapal correctly hidden | **DEPLOYED and verified live**; provider-gated tile behavior confirmed in production |
| Duplicate signup alert fix (API) | Once-per-account gate on the `signup_alerted` system_event + account-age window, carried by the deployed `6077883` API | **Deployed on Render**; live `#signups` behavior not yet observed over subsequent logins |

A live payment can only be claimed after a real verified transaction produces a
`payment:<tx_ref>` journal. Pesapal additionally requires live credentials and
an IPN endpoint reachable at the public API origin (the 0017 schema is already
applied). The read-only queries in
[`docs/FINANCIAL_LEDGER.md`](FINANCIAL_LEDGER.md) §8 and
[`docs/PAYMENTS_PESAPAL.md`](PAYMENTS_PESAPAL.md) §9 are the acceptance checks.

---

## 7. Reproduction prerequisites

* `.test-deps/` contains the Python test dependencies (pytest, requests, Flask,
  cryptography, PyJWT, Playwright 1.64 and greenlet 3.5.6), built for Python
  **3.14**. Use `/c/Users/HP/OneDrive/Python314/python.exe`; a 3.12 interpreter
  cannot load the native `greenlet` module and the E2E module skips.
* Playwright's Chromium is installed; the harness can fall back to the system
  Chrome channel when needed.
* `tests/sql/node_modules/` contains `@electric-sql/pglite 0.5.8`.
* Tests use `FLASK_ENV=development`, a fixed test `JWT_SECRET`, a placeholder
  Supabase URL/key, and faked network clients. No real secret is read or written.
* The SQL tests stub Supabase roles and `auth.uid()`; they do not connect to the
  live project.
* The E2E harness serves `static/` from disk, stubs Supabase REST/Auth and the
  OAuth settings endpoint, and aborts unrelated external requests, so no real
  Google or Pesapal call is made.
