# AgriBridge — Implementation Status

> **2026-10-22 update:** The baseline below is the original Phase 1 audit. Since
> then: `/api/pay/initiate` and the SMS/admin/order update paths are session- and
> ownership-checked; migration 0013 (ledger) and 0014 (internal-table lockdown)
> are applied; migration 0015 (APPLIED live — `orders.source_ref` returns 200,
> verified 2026-10-22) makes
> `create_order_atomic` category-aware
> (livestock/supplier pricing and attribution), and the SPA no longer inserts or
> updates orders directly. Migration 0016 adds farmer settlements in code:
> delivered+paid orders create one pending payout, and
> `POST /api/admin/payouts/<id>/settle` posts `settlement:<payout_id>` only with
> an operator-supplied provider reference and a posted receipt. Migration 0017
> adds Pesapal API 3.0 hosted checkout and recurring-charge recording; recurring
> money posts to `SUSPENSE_OVERPAYMENT`, never to a farmer. The SPA also offers
> Google sign-in through Supabase OAuth, with a locked onboarding modal for
> phone/role/district/terms. **Live schema and deployment status (verified
> 2026-10-22 by read-only PostgREST probes and public endpoint probes):** 0002,
> 0016 and 0017 are applied; `payment_gateway_state`, `payment_subscriptions` and
> `subscription_charges` exist and reject `anon` with `42501`. Push `9123b38`
> deployed the API on Render and the SPA via the `Deploy public web app`
> workflow. **0015 is now applied** — `orders.source_ref` returns HTTP 200 — so
> livestock/supplier checkout runs against the category-aware function.
> Push `6077883` restores a safe Bank Transfer method (always offered, real
> account details only from `BANK_TRANSFER_ACCOUNTS`, orders unpaid until an
> admin confirms via `POST /api/admin/orders/<id>/verify-bank-transfer`) and
> fixes duplicate Discord `#signups` alerts (fires once per account). The API on
> Render serves this (live `providers.bank = true`). The Cloudflare auto-deploy
> had **silently skipped** (green run, deploy step skipped), so the SPA has since
> been published from an authenticated workstation each time.
>
> **Deploy automation, now honest and working (2026-10-23):** `Deploy public web
> app` can no longer pass while publishing nothing — it exits 1 when a secret is
> missing, and on any wrangler failure it captures the output and republishes the
> reason as a check annotation plus the run summary. Both
> `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` exist in GitHub. After an
> invalid-token phase (`Authentication error [10000]` / `Invalid access token
> [9109]`), the owner replaced the token with one scoped to **Specified Workers →
> agribridge → Individual Workers Editor**; manual run `39859133726` succeeded
> and published Worker version `a6a1d85b-f203-4e90-8dd3-b405455c32c4`. CI
> publishing is restored; Cloudflare *Workers Builds* still fails and should be
> disabled so only one publisher targets the Worker
> (`docs/OPERATIONS_SETUP.md` §11, `docs/LIVE_BASELINE_AUDIT.md` §20).
>
> **Buyer-UI redesign (`74879f8`/`20f45f2`) merged red and was repaired in
> `db9d92f`:** the redesign had reintroduced the retired `support@agribridge.ug`
> contact, broken the pinned ruff gate, made 21 tests fail, and — most
> importantly — rewrote `syncPayMethods()` to read a provider cache it never
> fetched, so server-enabled payment options (Pesapal) and configured bank
> accounts could stay hidden on a fresh page load. Fixed and confirmed in
> **production** (the live page now populates `window._payProviders` on first
> checkout render; Bank tile visible, Pesapal correctly hidden while unconfigured).
> Live SPA = wrangler version `a6a1d85b-f203-4e90-8dd3-b405455c32c4`, published
> by CI run `39859133726` from commit `bb2cd7a`; live HTML is the Git blob of
> `static/index.html` (sha256 `439306a7…a19a13`; content-identical to the
> workstation file after CRLF normalisation).
> Current verified suite: **447 Python tests** (including 28 Playwright browser
> tests) and 129 SQL assertions; CI green on `db9d92f`, `30f8784` and `bb2cd7a`;
> the Stage 1 bundle is **published by the restored CI workflow and verified
> live** (see `docs/LIVE_BASELINE_AUDIT.md` §20); see `docs/TEST_RESULTS.md`.
>
> **Pesapal is now configured live (2026-10-22).** The owner added consumer
> credentials and `PESAPAL_ENV=live` in Render; `/api/pay/providers` returns
> `"pesapal": true`, `/health/live` is 200, and both Pesapal routes answer
> (`/api/pay/webhook/pesapal` 200, `/api/pay/pesapal/callback` 302). While
> tracing that config we found a **latent defect**: a truncated
> `PESAPAL_CALLBACK_BASE_URL` passed `_safe_ipn_url` (any https host is accepted,
> even `agribridge-1-`) and would have registered a dead IPN endpoint — money
> taken, order never marked paid, no ledger journal. `app.py` now resolves the
> base URL through `_resolve_pesapal_base_url()`, which requires a dotted
> hostname (localhost exempt), logs `pesapal.callback_base_url_rejected` with the
> fallback used, and otherwise uses `PESAPAL_BASE_URL_DEFAULT`. 5 regression
> tests cover it. Owner must still confirm the Render value is deleted or
> complete. **No real Pesapal payment has been made**, so the notify → verify →
> mark-paid → ledger chain stays unverified in production (Rule 11). Card and
> Flutterwave MTN remain unconfigured; `bank_accounts: []` until
> `BANK_TRANSFER_ACCOUNTS` is set. Note the Render build log installs `cp314`
> wheels while `render.yaml` pins `3.12.7` (CI uses 3.12.7) — see
> `docs/LIVE_BASELINE_AUDIT.md` §18.
> **Live-baseline audit:** `docs/LIVE_BASELINE_AUDIT.md` (2026-10-22) records the
> evidence-based production state, the first verified blocker (deploy automation
> reporting green while publishing nothing) and its fix (§13–§17).

## Stage 1 — Buyer storefront (2026-10-22) — DEPLOYED AND VERIFIED LIVE

The first slice of the buyer-interface work, in the owner's stated order:
**homepage → product cards → product details → shopping cart**. Checkout,
payment integration and delivery tracking are the next stages; this stage only
guards the existing checkout against out-of-stock lines. The admin panel,
financial ledger, API, database and dependencies are untouched.

**Changed files:** `static/index.html` (+987/−289) and the new
`tests/e2e/test_buyer_ui.py`. The Capacitor shell wraps the same HTML, so it
picks the change up when a new bundle is built.

- **Home** — real listings and stats, honest empty/error states, real rated
  farmers only, working search overlay and category/location filters (12
  districts persisted in `localStorage`), and a **Browse as guest** path out of
  the first-visit splash, which previously forced sign-up.
- **Cards** — real image/emoji fallback, discount and struck original price,
  stock/minimum/farmer/district, stars only from `farmer_ratings`, keyboard
  activation, and Add disabled when the listing cannot be bought.
- **Details** — real fields and gallery, data-backed ratings, conditional
  verification and delivery claims, out-of-stock actions disabled, normalised
  descriptions, related products.
- **Cart** — `localStorage` persistence, re-hydrated price/stock on every
  render, quantity bounded by stock and minimum, unavailable lines flagged,
  totals equal the item subtotal, delivery shown as "Arranged after
  confirmation" (orders store no delivery fee — `services/finance.py:25`), and
  checkout blocked while an unavailable line remains.
- **Signed-in buyers** — `applyRoleUI()` now lands a `vendor` on the buyer home
  instead of the market page.

Repeated browser runs also exposed two real navigation races, both fixed and
pinned: a late `loadHomeStats()` reset a category the buyer had just chosen
(`window._homeCat` is now re-applied), and a delayed guest role refresh at 300 ms
closed a product detail page (`showGuestLanding()` no longer steals navigation
when a page is already open).

**Verification:** 6 new Playwright tests (deterministic fixtures, real HTML;
5 consecutive green runs, 12.1–15.4 s; all 28 browser tests green twice);
447 Python tests pass (441 baseline + 6, 66.09 s); ruff clean under local 0.16.10
and CI-pinned 0.8.4; `node --check` passes all four inline script blocks; a
51/51 ad-hoc browser harness ran at mobile/desktop widths against the live
Supabase project **read-only** (no order placed, no row written).

**Production status: PUBLISHED BY CI AND VERIFIED.** The work is commit
`30f8784` on `main`; CI (`Backend lint + type + tests`, `Migration 0007`) is
green. The `Deploy public web app` workflow is working again: after the owner
replaced `CLOUDFLARE_API_TOKEN` with a token scoped to **Specified Workers →
agribridge → Editor**, manual run `39859133726` succeeded and published Worker
version `a6a1d85b-f203-4e90-8dd3-b405455c32c4`. Both
`https://agribrige.com/` and the workers.dev host serve the Git blob of
`static/index.html` at `bb2cd7a`: 752,146 bytes, sha256
`439306a7…a19a13`, content-identical to the workstation file after CRLF
normalisation, with all Stage 1 markers. A production browser smoke test went
landing → Launch app → splash → Browse as guest → home cards → category filter →
detail → add to cart → cart, with honest totals and **zero page errors** (no
order placed). Previous version `bb9e65fb-974d-4381-8bf0-c78191a2e6d0` is the
rollback target. Workers Builds still fails and must be disabled so only one
publisher targets the Worker; see `docs/LIVE_BASELINE_AUDIT.md` §20.

**Residual / out of scope:** cart is device-local (no server cart); promo codes
remain deliberately absent; the legacy B2B invoice generator still hardcodes a
UGX 25,000 delivery and 6% commission and submits nothing — flagged, not
endorsed; checkout/payment/delivery-tracking behaviour is Stage 2/3 work.

---

**Phase 1 COMPLETE — Baseline Audit**
**Completed:** 2026-10-10
**Auditor:** Claude Code (baseline inspection, no live environment access)

---

## What Was Inspected

| File / Area | Coverage |
|-------------|----------|
| `app.py` (1,907 lines) | Full read — all routes, auth helpers, service wiring, payment integrations |
| `static/index.html` (~6,685 lines) | Partial read (CSS + structure; JS reviewed for auth calls and hardcoded values) |
| `static/business-config.js` | Full read |
| `supabase/base-schema.sql` | Full read — all 21 tables, all RLS policies |
| `supabase/migrations/0001–0007` | Full read for 0001, 0002, 0004, 0007; skimmed 0003, 0005, 0006 |
| `services/config.py` | Full read |
| `services/orders.py` | Full read |
| `services/` (other files) | File list reviewed; individual files not read (discord, legal, matching, notifications, push, supabase_client, account, worker, business, monitoring, logging) |
| `tests/test_api.py` | Full read |
| `tests/` (other test files) | File list reviewed |
| `mobile/capacitor.config.json` | Full read |
| `mobile/package.json` | Full read |
| `render.yaml` | Full read |
| `requirements.txt` | Full read |
| `GO-LIVE.md` | Full read |
| `docs/` existing files | File list reviewed |

---

## Key Findings Summary

### Architecture (Sound)
- Flask backend on Render (2 gunicorn workers, Python 3.12)
- Supabase (Postgres + GoTrue Auth + PostgREST) for persistence and auth
- Single-file frontend (`static/index.html`) served as a PWA
- Capacitor shell for mobile (Android/iOS), wrapping the same HTML
- Africa's Talking for USSD (*789#) and SMS
- Flutterwave for payment (pluggable; off until keys are set)
- Gemini (crop doctor) + Groq (AI assistant) for AI features
- Discord + FCM push for operator notifications
- Sentry for error monitoring

### Security Posture (Mixed — better than average for stage, not production-ready)
- Server-side order creation with atomic stock reservation: DONE
- GoTrue identity verification (buyer_id from session, not body): DONE
- Fail-closed config validation on startup: DONE
- CORS restricted to known origins: DONE
- RLS enabled on all tables: DONE
- Admin login rate-limited: DONE
- Flutterwave webhook re-verification: DONE
- Legal consent framework built: DONE (not enforced)
- **Migration 0002 (blocking direct order inserts) and migration 0015 are both applied and the new SPA is live.** `create_order_atomic` is now category-aware (`orders.source_ref` returns 200, verified 2026-10-22). Livestock/supplier pricing resolves server-side from each item's own crop listing.
- **Admin panel uses shared-password JWT**, not Supabase identity
- **In-memory rate limiting and USSD sessions** — not safe with 2 workers

### Operational Readiness (Partial)
- SMS/USSD: sandbox only
- Payments: COD/direct MoMo only (no gateway live)
- Email receipts: not active
- Legal enforcement: off
- Farmer payouts: not built
- Database backups: documented but not automated

### Test Coverage (Good for API contract, thin for business logic)
- API contract tests: health, order auth, state machine, admin identity, webhook, push registration
- Missing: USSD route unit tests, livestock order path, matching service integration

---

## Outstanding Issues by Priority

### Must Fix Before Any Meaningful Live Traffic

| # | Issue | Action |
|---|-------|--------|
| 1 | Default admin password `agribridge2026` may still be set | Set strong `ADMIN_PASSWORD` in Render immediately |
| 2 | ~~Migration 0015 not applied~~ — RESOLVED 2026-10-22: 0015 is applied and `orders.source_ref` returns 200, so `create_order_atomic` is category-aware | Confirm a livestock and a supplier order price correctly in the live checkout |
| 3 | USSD sessions in-process dict — breaks with 2 workers and on restart | Switch to `ussd_sessions` table in Supabase |
| 4 | `farmer_ratings` table referenced in matching function does not exist | Create table in a migration |

### Should Fix Before Scale

| # | Issue | Action |
|---|-------|--------|
| 5 | In-process rate limiting | Switch to Redis-backed or Cloudflare edge rate limiting |
| 6 | `unsafe-inline` in CSP | Refactor JS out of the monolithic HTML file |
| 7 | Admin routes use self-signed JWT, not Supabase identity | Migrate to `require_admin_user()` |
| 8 | `pay_initiate` unauthenticated | Add `verify_supabase_user()` guard |
| 9 | `_auth_role()` reads client-writable `user_metadata` | Use `user_roles` table instead |
| 10 | Correlation ID logged without sanitization | Validate/strip header value |

### Should Do Before Play Store Release

| # | Issue | Action |
|---|-------|--------|
| 11 | `targetSdkVersion` not set in Capacitor config | Set to 34 in `capacitor.config.json` |
| 12 | No native push, biometrics, or deep links | Decide scope for native features |

---

## Phase 2 — Recommended Next Steps

1. **Security**: Apply the items in "Must Fix" above. Set a real `ADMIN_PASSWORD`. Confirm migration 0001 is applied to the live Supabase project (it may already be — no DB access to verify).

2. **DB migrations**: Apply migrations 0003, 0004, 0005, 0006 (all flagged as safe in their headers). Create the `farmer_ratings` table. Only then apply 0002 after the frontend livestock/supplier order cutover is done.

3. **Legal rollout**: Follow the six-step process in GO-LIVE.md §Legal. Apply migration 0007, deploy the updated web app, run `publish_legal.py`, smoke test, then set `ENFORCE_LEGAL_ACCEPTANCE=true`.

4. **Africa's Talking**: Move from sandbox to live API key + SMS sender ID. Test USSD end-to-end on a real handset.

5. **Payments**: Set up Flutterwave sandbox, run the full checkout-to-webhook flow, then go live.

6. **Email receipts**: Set up Resend, deploy the `send-receipt` Edge Function with `RESEND_API_KEY`.

7. **USSD session persistence**: Replace the in-memory dict with the `ussd_sessions` Supabase table.

8. **Rate limiting**: Add Redis to Render or switch to Cloudflare rate limiting rules.

9. **Backups**: Follow GO-LIVE.md §4 to set up GitHub Actions automated backup.

10. **Mobile**: Set `android.targetSdkVersion: 34` in `capacitor.config.json`. Run `cap sync android` and test the build. Address Play Store listing requirements.

---

## Files Created This Audit

- `docs/PRODUCTION_AUDIT.md` — full findings with severity ratings, route inventory, DB inventory, security analysis
- `docs/IMPLEMENTATION_STATUS.md` — this file

---

## Unread Files (Recommend Review Next)

These service files were listed but not read during this audit. Review them for their own security posture if making changes in those areas:

- `services/account.py` — account deletion logic
- `services/matching.py` — offer routing service
- `services/notifications.py` — notification dispatcher
- `services/push.py` — FCM integration
- `services/supabase_client.py` — Supabase wrapper
- `services/worker.py` — background matching worker
- `services/legal.py` — legal service
- `services/discord.py` — Discord notifier
- `static/index.html` (JS portion, lines 709–6685) — full frontend logic, all API calls, checkout flow
- `static/admin.html` — admin dashboard frontend
- `tests/conftest.py` and other test files — mock fixtures and coverage details
- `supabase/migrations/0003, 0005, 0006` — notification outbox, offer lifecycle, account deletion migrations
- `scripts/build_legal.py`, `scripts/publish_legal.py`, `scripts/migrate_supabase.py`
