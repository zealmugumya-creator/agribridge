# AgriBridge — Implementation Status

> **2026-10-22 update:** The baseline below is the original Phase 1 audit. Since
> then: `/api/pay/initiate` and the SMS/admin/order update paths are session- and
> ownership-checked; migration 0013 (ledger) and 0014 (internal-table lockdown)
> are applied; migration 0015 (still unapplied live) makes
> `create_order_atomic` category-aware
> (livestock/supplier pricing and attribution), and the SPA no longer inserts or
> updates orders directly. Migration 0016 adds farmer settlements in code:
> delivered+paid orders create one pending payout, and
> `POST /api/admin/payouts/<id>/settle` posts `settlement:<payout_id>` only with
> an operator-supplied provider reference and a posted receipt. Migration 0017
> adds Pesapal API 3.0 hosted checkout and recurring-charge recording; recurring
> money posts to `SUSPENSE_OVERPAYMENT`, never to a farmer. The SPA also offers
> Google sign-in through Supabase OAuth, with a locked onboarding modal for
> phone/role/district/terms. **Live schema status (verified 2026-10-22 by
> read-only PostgREST probes):** 0002, 0016 and 0017 are applied;
> `payment_gateway_state`, `payment_subscriptions` and `subscription_charges`
> exist and reject `anon` with `42501`. **0015 is still missing** —
> `orders.source_ref` does not exist. Apply 0015 **before deploying this SPA
> bundle**, because 0002 has already closed direct order inserts and the legacy
> `create_order_atomic` is not category-aware. Pesapal and Google are
> unconfigured and unverified live. Current verified suite: 417 Python tests
> (including 22 Playwright browser tests) and 129 SQL assertions; see
> `docs/TEST_RESULTS.md`.

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
- **Migration 0002 (blocking direct order inserts) was not applied at baseline; it is now applied. Migration 0015 remains missing**, so the live `create_order_atomic` is not category-aware. Apply 0015 before the new SPA checkout goes live.
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
| 2 | Migration 0015 not applied — live `create_order_atomic` lacks category-aware livestock/supplier pricing | Apply 0015 before deploying this SPA bundle; 0002 is now applied, so direct inserts are already closed |
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
