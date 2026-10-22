# AgriBridge — Live-Baseline Audit

**Audit run date:** 2026-10-22 (evidence timestamp `2026-10-22T06:17:51Z` unless noted)
**Method:** read-only inspection of the repository, deployment records and public
production endpoints. No production mutation was performed to obtain a result.
**Legend:** VERIFIED BLOCKER · VERIFIED DEFECT · CONFIGURATION GAP · UNVERIFIED · WORKING · INFORMATIONAL

> This audit is deliberately separate from the historical `PRODUCTION_AUDIT.md`
> and `IMPLEMENTATION_STATUS.md`, which are preserved as the audit trail. Findings
> here are labelled by the kind of evidence behind them: **CODE** (source/CI),
> **LIVE** (a production endpoint or DB probe), **DEPLOY** (a workflow/run record),
> or **ASSUMPTION** (needs access we do not have).

---

## 1. Repository and deployed versions — WORKING (CODE)

- Branch `main`, HEAD `8e954ac`, working tree clean, in sync with `origin/main`. No uncommitted user changes were found; none were discarded.
- Backend reports `version: 3.3.0` (**LIVE** `/health`).
- `render.yaml`: Python `3.12.7`, `gunicorn app:app --workers 2`, `healthCheckPath: /health/live` (**CODE**). Note the deploy target runs 3.12 while the local `.test-deps` suite runs 3.14 — CI is the authoritative environment.

## 2. Service health and deployment status

- Backend readiness: `GET /health/live` → **200**; `/health` → `status:ok`, `supabase_connected:true` (**LIVE**). → **WORKING**
- Public SPA: `https://agribrige.com/` serves the current bundle; `GET /` contains `pm-bank` and `/admin` (via `/admin.html` → 307 → `/admin`) contains `verifyBankTransfer`/`Bank paid` (**LIVE**). → **WORKING**
- **VERIFIED BLOCKER — deployment automation passes while deploying nothing.** The `Deploy public web app` workflow run for push `6077883` finished with conclusion `success`, but its `Deploy Worker static assets` step conclusion was `skipped` (**DEPLOY**, run id `39721847981`). The workflow `exit`s green whenever `CLOUDFLARE_API_TOKEN` is absent (`deploy-cloudflare.yml:26-37`). Proof of impact: the live site did **not** contain the bank card until the SPA was published **manually** with `wrangler deploy` (worker version `15ff5212`). A green deploy run is therefore **not** evidence that a release shipped. See §12.

## 3. Database and migration status — WORKING (LIVE, read-only PostgREST probes)

Applied to live Supabase `lvloaweijidgcuezdnmu`, verified by probing columns/tables that each migration creates:

| Migration | Probe | Result | Label |
|---|---|---|---|
| 0013 ledger | `ledger_accounts` select | `401` (exists, RLS-locked to non-anon) | WORKING |
| 0014 lockdown | `system_events`/`transporters` anon | `401` (prior session) | WORKING |
| 0002 admin identity | `user_roles` select | `200 []` through RLS | WORKING |
| 0015 category pricing | `orders.source_ref` | `200 []` | WORKING |
| 0016 settlements | `payouts.commission_amount` | `200` | WORKING |
| 0017 Pesapal | `payment_subscriptions` anon | `401` | WORKING |

- The live schema matches the application's assumptions for these objects. No repository migration is absent from the live DB (**LIVE**). → **WORKING**

## 4. Authentication and authorization findings

- Anonymous reads of ledger and payment tables are rejected (`401`) — effective RLS, not just policy names (**LIVE**). → **WORKING**
- `POST /api/admin/orders/<id>/verify-bank-transfer` without an admin token → **401** (**LIVE**). Admin gate enforced server-side. → **WORKING**
- Admin panel still authenticates with a shared-password JWT (`ADMIN_PASSWORD`) rather than Supabase identity in some paths (historical F01, **CODE**). Not re-verified as exploitable this run. → **UNVERIFIED** (need to confirm the password path is not reachable for privileged writes on live).

## 5. Checkout and payment status

- Live `/api/pay/providers`: `airtel,momo,cod,direct,bank = true`; `card,flutterwave,mtn_momo,pesapal = false`; `bank_accounts: []` (**LIVE**).
- Bank Transfer is offered and orders stay unpaid until admin verification; the verification endpoint marks paid using the **stored** order amount and posts the idempotent `payment:<ref>` journal (**CODE** + tests). UI + API are live (**LIVE**). → **WORKING** (code path) / **UNVERIFIED** (no real bank order placed and settled live).
- Pesapal / Card / Flutterwave MTN: implemented but **disabled** because credentials are absent (`providers.*=false`) (**LIVE**). → **CONFIGURATION GAP** (owner credentials required; see §11).
- Google sign-in: code deployed; buttons render only when Supabase reports the provider enabled. Provider not yet configured (**CODE**; not probed live). → **CONFIGURATION GAP**.
- The full buyer journey (authenticated `POST /api/orders` → confirm) was **not** exercised end-to-end against production; we have no user session and Rule 11 forbids real payments. → **UNVERIFIED**.

## 6. Financial ledger status

- Ledger core (0013) live and RLS-locked. Payment/settlement/COD journaling is proven by 431 unit/API tests and 129 SQL assertions against real PGlite (**CODE**). → **WORKING** (tests).
- No verified incorrect/duplicate posting exists, but no **real** production transaction has been journaled this run. → **UNVERIFIED** (live posting requires a real payment, which we will not fabricate).

## 7. Notifications and phone push status

- Discord feed: `discord_configured:true` (**LIVE** `/health`).
- **VERIFIED DEFECT (fixed this session, live effect UNVERIFIED):** duplicate `#signups` alerts fired on every login (owner screenshots showed the same user three times). The deployed code now fires once per account via a durable `signup_alerted` `system_events` marker + an account-age gate (**CODE**, deployed to Render). Subsequent logins have **not** been observed to confirm the suppression in production. → fix is **UNVERIFIED live**.
- FCM mobile push: implemented; admin push requires Firebase service-account credentials (§11). → **CONFIGURATION GAP** / **UNVERIFIED**.

## 8. Order, farmer, supplier and transporter workflows

- Order creation routes through `POST /api/orders`; direct browser inserts are closed by 0002 (**LIVE** RLS, **CODE**).
- Category-aware pricing for livestock/supplier is active via 0015 (**LIVE** schema). Not exercised with a real order. → **UNVERIFIED**.
- Farmer matching, offers and payouts are implemented (0004a/0005/0016) and tested in PGlite (**CODE**). Live behavior **UNVERIFIED**.

## 9. Delivery tracking status

- Transporter tables are RLS-locked (**LIVE**). Tracking map fix shipped earlier (**CODE**). Live tracking flow not exercised. → **UNVERIFIED**.

## 10. Automated test status — WORKING (CODE)

- Python: **431 passed** (incl. 22 Playwright) on Python 3.14 with `.test-deps`. → PASS
- SQL (PGlite): **129 assertions passed** across 6 suites. → PASS
- Lint: ruff clean on both local 0.17 and CI-pinned 0.8.4. → PASS
- CI: green on `319bae9`/`8e954ac`. **Note:** CI **failed** on `6077883` at "Lint (ruff)" (a `UP017` in the new test), fixed in the follow-up commit — an example of a real check correctly failing. → PASS (after fix)
- Browser E2E against **live** services: NOT RUN (stubs only). → NOT RUN

## 11. Missing credentials, permissions or external access

Required settings present/absent (names only; values never inspected or logged):

- **CONFIGURATION GAP:** `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` — **absent** from GitHub Actions secrets (this is the root cause of §2's blocker: SPA deploys silently skip).
- **CONFIGURATION GAP:** `BANK_TRANSFER_ACCOUNTS` — empty in Render, so checkout shows support contact instead of a real account (intended safe behavior, but the operator must supply the real details).
- **CONFIGURATION GAP (owner credentials):** Flutterwave `FLW_*`, Pesapal `PESAPAL_*`, Firebase `FCM_*`, `SENTRY_DSN`, Supabase service-role `SUPABASE_KEY` (Render-only), GitHub backup secrets.
- **ASSUMPTION / blocked access:** no service-role or authenticated user session was available, so real order placement, real payment/webhook, admin login, and live dashboard/RLS internals beyond anonymous probes could not be exercised.

## 12. Prioritized verified blockers

Per the selection order (security → financial integrity → outage → buyer journey → fulfillment → admin controls → deployment automation):

1. **VERIFIED BLOCKER — Deployment automation reports success while deploying nothing** (§2). Highest item with concrete, reproducible evidence. Items ranked above it are either **WORKING** (RLS, readiness, schema) or **UNVERIFIED** (live buyer journey, live payments) — none rises to a *verified* blocker this run, so per the rules it would be wrong to label an unverified assumption as the blocker.
2. **VERIFIED DEFECT — duplicate signup alerts** (§7): real and reproduced via owner evidence; remediated in deployed code; confirmation pending observation of further logins.
3. **CONFIGURATION GAPs** (§11): Cloudflare token, bank accounts, payment/notification providers — owner actions, not code defects.
4. **UNVERIFIED** live flows (payments, order journey, tracking): require a real session/transaction or owner-authorized sandbox; deliberately not exercised to avoid production mutation (Rule 11).

**Next highest-priority blocker after #1** is to confirm the deployed signup-suppression actually stops duplicates on real logins, and to verify one real unpaid order (COD or bank) reaches the buyer journey — both gated on an authenticated session we do not have.

---

## 13. Fix record — blocker #1 (deployment automation)

**Status:** implemented on branch `fix/deploy-workflow-honest-status`; **not pushed, not deployed** (per the run's git rule: show the summary and obtain approval first).

**Diagnosis**
- **What is broken:** `deploy-cloudflare.yml` finished green while publishing nothing. Evidence: run `39721847981` (push `6077883`) — job `success`, `Deploy Worker static assets` step `skipped` because the step was gated on `if: env.CLOUDFLARE_API_TOKEN != ''`; the empty-token path only echoed a warning. Impact: the bank-transfer checkout (and any `static/` change) silently never shipped until published manually.
- **Expected behavior:** a deploy run is green **only if** the artifact was published; if it cannot deploy, it must fail.
- **Root cause:** the workflow encoded "skip" as a non-failing outcome, conflating *no-op* with *success*.
- **Files involved:** `.github/workflows/deploy-cloudflare.yml`; new test `tests/test_deploy_workflow.py`.
- **What it could break:** a red deploy run is a deliberate behavior change (previously green-on-skip). No branch protection depends on this job; unrelated workflows (`ci.yml`, `db-backup.yml`) are untouched. The happy path (`wrangler@4 deploy`) is unchanged. Rollback = revert the one YAML edit.

**Fix:** collapsed the warn-then-skip step pair into a single deploy step that fails loudly (`exit 1` + `::error::` + step-summary guidance) when `CLOUDFLARE_API_TOKEN` or `CLOUDFLARE_ACCOUNT_ID` is missing, and otherwise runs `npx --yes wrangler@4 deploy`.

**Tests (commands run this session)**
- `pytest tests/test_deploy_workflow.py` → **3 passed** after fix; **2 failed / 1 passed before fix** (proves the regression is caught).
- Full suite `pytest -q` → **434 passed**. Ruff (CI-pinned 0.8.4) on the test + workflows → clean.
- YAML validity via `js-yaml`: parses OK; parsed deploy step has **no `if` skip-guard**, contains the empty-token test and `exit 1`, and still contains `wrangler@4 deploy`.
- **Runtime simulation** of the step's shell with both secrets unset → `::error::` printed, step summary written, **exit code 1**, and `exit 1` precedes the `wrangler` line so no deploy is invoked. Classified: PASS (mocked; not a live GitHub run).

**Production status:** nothing pushed or deployed for this fix. The live SPA remains on the manually-published `6077883` bundle (`pm-bank` verified live). When the owner later adds the two secrets, this workflow will actually publish and the green run will be trustworthy.

**Remaining risk / operator action:** the underlying `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` are still absent (§11) — the fix makes the missing-credential state *visible and safe*, not *working*. Adding the secrets is the owner action that turns SPA auto-deploy on.
