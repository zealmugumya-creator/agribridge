# Security Review — PostgREST exposure audit

Date: 2026-10-21
Project: `lvloaweijidgcuezdnmu` (`https://lvloaweijidgcuezdnmu.supabase.co`)
Scope: anonymous/authenticated access to internal tables reported by the administrator.
Method: read-only PostgREST probes using the public publishable key, plus a
non-mutating write probe (an insert with a NULL required column). No production
rows were created, changed, or deleted.

## Confirmed findings

### 1. CRITICAL — `system_events` readable and writable by anonymous users

Evidence collected before the fix:

- `GET /rest/v1/system_events?select=*&limit=1` returned `200` with an internal
  event (`order_failed`, buyer UUID, error details).
- `POST /rest/v1/system_events` with `{"event_type": null}` returned PostgreSQL
  error `23502` (NOT NULL violation), not a permission error. This proves the
  `anon` role held INSERT privilege and the row-level policy allowed the write.

Root cause: `migrations/0008_transporters_and_system_events.sql` created

```sql
create policy "service role full access" on public.system_events
  using (true) with check (true);
```

Without a `TO service_role` clause, a policy applies to `PUBLIC` — every role,
including `anon`. Supabase's default table grants then made the policy effective.

Impact: anyone holding the public key could read operational events (including
buyer identifiers and failure details), forge events, or delete the error log.

Fix:

- `supabase/migrations/0014_lockdown_internal_tables.up.sql` drops the unscoped
  policy, creates a `service_role`-only policy, and revokes all privileges from
  `public`, `anon`, and `authenticated`.
- The legacy `migrations/0008_transporters_and_system_events.sql` script was
  patched with `FOR ALL TO service_role` so re-running it cannot reopen the hole.
- Covered by `tests/sql/security_lockdown.test.mjs`.

### 2. HIGH — `transporters` fully exposed to anonymous users

Same unscoped policy on `public.transporters`. The table was empty during the
audit, so no data leaked in the sample, but `anon` held full table privileges
and the policy allowed every row. Authenticated owner policies also existed, but
they grant access rather than restrict the PUBLIC policy.

Fix in 0014:

- `service_role` keeps full access.
- `authenticated` keeps `select`/`update` with existing owner-only policies
  (`supabase_uid = auth.uid()`); `insert`/`delete` are revoked.
- `anon` loses all privileges.

No client code reads or writes these tables directly; transporter management is
server-side (`service_role`) and transporter self-service uses the authenticated
owner policies.

### 3. HIGH — SMS endpoints were an unauthenticated relay

`POST /api/notify-order` and `POST /api/sms-receipt` required no session. Anyone
could name a farmer id or a phone number and make AgriBridge send SMS to it on
AgriBridge's Africa's Talking account. That is an SMS-toll abuse vector and lets
an attacker text arbitrary numbers with AgriBridge branding (the receipt text
also echoed a client-supplied total).

Fix:

- `/api/notify-order` is removed. Farmer SMS is already sent server-side inside
  `POST /api/orders` after the order is committed, so no functionality is lost.
- `/api/sms-receipt` now requires a verified Supabase session. It accepts only an
  order reference; recipient phone and amount come from the caller's own order
  rows. Unknown and foreign orders both return `404` (no order-existence
  oracle). Payment receipts are sent only by the verified payment path.
- The browser no longer sends a phone number or total for receipts.

Evidence: `tests/test_identity_and_admin.py` (anonymous `401`, foreign order
`404`, body-supplied phone/total ignored, removed endpoint `404`).

### 4. HIGH — admin generic editor could rewrite order and payment state

`POST/PATCH /api/admin/row` allowed `orders.status` and `orders.payment_status`
in its column allowlist. An admin token could move an order to `delivered`
without `set_order_status` (skipping stock release and `order_events`) or mark
it `paid` without any gateway verification or ledger posting.

Fix:

- Both columns are removed from the generic allowlist; a request naming either
  is rejected with `400` instead of being silently dropped.
- `PATCH /api/admin/order/<id>/status` now calls the `set_order_status` Postgres
  state machine (`404` for a missing order, `409` for an illegal transition) and
  records a `order_status_changed` system event.
- The admin console edits only delivery address and notes, and advances status
  through a dedicated dropdown that calls the state-machine endpoint.
- The farmer dashboard had the same bypass from the other side: its Confirm /
  Reject / Deliver buttons called `sbUpdate('orders', {status})` directly from
  the browser. They now call `POST /api/orders/<id>/status`, which verifies the
  session, checks that the caller is the order's farmer (or an admin), and runs
  `set_order_status`; the UI follows the legal `confirmed → in_transit →
  delivered` path instead of jumping straight to delivered.
- Payment status is no longer writable from any generic editor. It changes only
  when a gateway payment is verified, or when a `cash` order is delivered and
  the COD journal posts first (`cod:<ref>`); an unpaid gateway order stays
  unpaid at delivery confirmation. See [`docs/FINANCIAL_LEDGER.md`](FINANCIAL_LEDGER.md) §3.

Evidence: `tests/test_identity_and_admin.py`.

### 5. MEDIUM — transporter GPS writes overwrote finished deliveries silently

`POST /api/transporter/location` updated every delivery row for the transporter,
including delivered and failed ones, and ignored the result of both writes. A
transporter could move the recorded end position of historical deliveries, and a
database outage still returned `{"ok": true}`.

Fix: only `picked_up`, `in_transit` and `at_checkpoint` deliveries are updated
(by row id), completed rows are untouched, and a failed write returns `503`.

Evidence: `tests/test_identity_and_admin.py`.

## Verified as not exploitable

The following tables returned zero rows to `anon` and their policies are
correctly scoped (`TO service_role` / owner checks):

`admin_audit_log`, `admin_devices`, `notification_outbox`, `orders`,
`user_roles`, `web_push_subscriptions`, `payouts`, `contact_messages`,
`account_deletion_requests`, `privacy_requests`.

Note: a `has_table_privilege(...)` query can return `true` because Supabase
grants table privileges by default while RLS still filters rows. Grants alone do
not prove exploitability; the probes above test actual access. `system_events`
was the exception because its policy explicitly allowed every role.

## Open findings (not changed in this migration)

| Severity | Finding | Recommendation |
|---|---|---|
| Medium | `farmers` is intentionally public (`farmers_public_select`) and exposes `email`, `phone`, full name, village/sub-county. No web/mobile code reads it directly today. | Decide whether buyer-facing profiles need `email`. If not, revoke column `select (email)` from `anon, authenticated`, or serve profiles through a narrow public view. |
| Low | `platform_config` is public and exposes `commission_pct` and `payout_note`. No secrets present. | Keep public only if the app needs it; otherwise move to a safe view. |
| Info | `legal_document_versions`, `listings`, `animal_listings`, `market_prices`, `supplier_products`, `training_videos` are intentionally public catalog data. | No action; review before adding sensitive columns. |

## Live verification steps

Applied in the Supabase SQL Editor on 2026-10-21. Post-fix probes:

| Probe | Before | After |
|---|---|---|
| anon `GET /system_events` | `200` + internal rows | `401` permission denied |
| anon `POST /system_events` | `23502` (write privilege) | `401` permission denied |
| anon `GET /transporters` | `200` (empty) | `401` permission denied |
| anon `GET /ledger_account_balances` | `401` | `401` |

Reproduce with:

```bash
# expect 401 permission denied (was 200 with data)
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://lvloaweijidgcuezdnmu.supabase.co/rest/v1/system_events?select=*&limit=1" \
  -H "apikey: <publishable-key>" -H "Authorization: Bearer <publishable-key>"

# expect 401, not 23502
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "https://lvloaweijidgcuezdnmu.supabase.co/rest/v1/system_events" \
  -H "apikey: <publishable-key>" -H "Authorization: Bearer <publishable-key>" \
  -H "Content-Type: application/json" -d '{"event_type":null}'
```

Also confirm the admin ⚠ Errors tab still loads (it reads through the backend
service role) and that transporter self-service still works for a logged-in
transporter.

## Test evidence

`cd tests/sql && npm install && npm test`

- 17 security assertions pass (vulnerability reproduced before the fix, denied
  after; owner isolation; idempotent re-apply; down migration keeps the revokes).
- Existing legal-consent and ledger SQL suites continue to pass.
- `tests/test_identity_and_admin.py`: 10 checks pass covering SMS auth/scoping
  and the admin status boundary; full pytest suite is 338 passed / 1 skipped
  (Playwright not installed).
