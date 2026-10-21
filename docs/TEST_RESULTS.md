# AgriBridge — Test Results

**Date:** 2026-10-21
**Stage:** Identity-scoped SMS, admin write boundaries and cash-on-delivery posting
**Environment:** Windows, Python 3.12, repository `C:\Users\HP\Documents\agribridge`

Every result below was produced by a command run in this session. Live payment
posting has **not** been exercised against Flutterwave; the code paths are
covered with faked provider/RPC responses. See §5.

---

## 1. Python unit and API tests

```bash
PYTHONPATH=.test-deps python -m pytest -q
```

```
362 passed, 1 skipped, 87 warnings in 13.37s
```

The skipped test is `tests/e2e/test_consent_ui.py`; it requires Playwright,
which is not installed in this environment. That is a missing browser-test
dependency, not a passing E2E claim.

Targeted payment/ledger run:

```bash
PYTHONPATH=.test-deps python -m pytest tests/test_ledger.py tests/test_payments.py \
  tests/test_card_payments.py tests/test_admin_finance.py tests/test_settlements.py -q
```

```
96 passed, 65 warnings in 3.74s
```

## 2. Lint

```bash
PYTHONPATH=.test-deps python -m ruff check app.py services/ scripts/ tests/
```

```
All checks passed!
```

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

The ledger suite applies migration 0013, verifies account seeding, balanced and
unbalanced journals, idempotent replay, append-only guards, reversal rules,
admin/anon access, and rollback. It also posts the exact backend payload shape
(two-decimal string amounts, null actor) to prove the app's journal format is
accepted by Postgres. The security suite reproduces the original
anonymous-read vulnerability and proves migration 0014 closes it. The category
suite applies migration 0015 and proves livestock/supplier prices and owners are
resolved from `animal_listings` / `supplier_products` (client prices ignored),
stock is decremented, cancellation restores it, oversell is rejected, produce
reservation is unchanged, free-form items still work, and the down migration
restores 0007a. The settlements suite applies migration 0016 and proves payouts
are created once for delivered+paid orders, commission is snapshotted,
backfill is idempotent, `mark_payout_paid` refuses without a posted settlement
journal and is callable only by the service role, and the down migration keeps
financial and audit records.

## 4. New coverage in this stage

`tests/test_ledger.py`:

* Full receipt: debit clearing, credit farmer payable.
* Cancelled order value credited to `CUSTOMER_REFUND_PAYABLE`.
* Overpayment credited to `SUSPENSE_OVERPAYMENT`; journal still balances.
* Exact Decimal arithmetic (`10000.10 + 20000.20` has no float drift).
* Server-generated `total_amount` wins over a legacy client `total_price`.
* Quantity × price fallback when totals are absent.
* Underpayment, zero/negative/non-numeric amounts, zero-total checkouts and
  non-UGX currency are rejected before any database call.
* RPC failure, transport exception, missing journal id, and invalid references
  are returned as structured failures (never raised into the payment path).
* Stable reference `payment:<tx_ref>`, `p_actor = null`, JSON-safe metadata.

`tests/test_payments.py`:

* A successful webhook verification marks the checkout paid **and** posts the
  ledger with the verified amount, provider and Flutterwave id.
* Underpayment, wrong reference, non-UGX, missing currency and non-successful
  provider status post nothing.
* A failed order-status write returns failure and does not post.
* A ledger failure raises an operator event but does not turn a paid order into
  a failed payment (prevents a second charge).
* A notification failure does not change the payment result.

`tests/test_card_payments.py`: the shared fixture now fakes the ledger RPC, so
card and OTP tests exercise verification without network access.

`tests/test_identity_and_admin.py` (this stage):

* Anonymous `POST /api/sms-receipt` is rejected with `401`; a removed
  `/api/notify-order` returns `404`.
* A foreign order reference returns `404` and sends nothing; a missing
  reference returns `400`.
* A caller's own order sends exactly one SMS, to the phone stored on the order,
  with the server-computed total (a body-supplied phone and total are ignored).
* The generic admin editor rejects `orders.status` and `orders.payment_status`
  with `400` and performs no write; delivery address still updates.
* `PATCH /api/admin/order/<id>/status` calls `set_order_status`; illegal
  transitions return `409` and a missing order returns `404`.
* Transporter GPS updates target only `picked_up`/`in_transit`/`at_checkpoint`
  deliveries by id (never completed rows) and report `503` when the write fails.
* The farmer dashboard no longer writes `orders` directly: a static check
  asserts that `static/index.html` contains no `sbUpdate('orders'` call and
  that its actions use `POST /api/orders/<id>/status`.

`tests/test_admin_finance.py` (delivery and COD):

* Delivery confirmation advances through `set_order_status` and writes only
  `deliveries.actual_arrival`; it no longer writes `orders.delivered_at`
  (a column the migrations do not define).
* An unpaid `cash` order posts a `cod:<ref>` journal (debit `COD_CASH_HELD`),
  then marks the order paid; a ledger failure returns `503` and leaves it unpaid.
* An unpaid `momo`/gateway order stays unpaid at delivery and posts nothing.
* Custom COD lines and reference/kind are also covered in `tests/test_ledger.py`.

`tests/test_settlements.py` (this stage):

* Settlement journal: debit `FARMER_PAYABLE` gross, credit
  `COMMISSION_REVENUE`, credit the receipt's asset account (`PROVIDER_CLEARING`
  or `COD_CASH_HELD`); zero commission and 100% commission both balance.
* Payout rows whose commission and net do not add up to gross are rejected
  before any RPC call.
* The settlement journal uses `settlement:<payout_id>`, kind
  `farmer_settlement`, **null** `payment_ref`, and records the provider
  reference in metadata.
* The endpoint requires admin auth, a UUID payout, and a non-empty printable
  provider reference.
* It refuses unknown/non-pending payouts, orders that are not delivered and
  paid, a payout whose gross no longer matches the order, a missing receipt, an
  ambiguous receipt, and an unknown receipt channel.
* Success posts the journal first, then calls `mark_payout_paid`; a journal
  failure returns `503` and never marks paid; a status-update failure is
  reported as retryable; a concurrent settlement returns `already_settled`.
* A posted settlement writes `admin_audit_log` (`payout.settled`) when the
  service-role write succeeds.

## 5. Live verification status

| Item | Evidence | Status |
|---|---|---|
| Migration 0013 (ledger core) | Supabase SQL Editor: "Success. No rows returned"; SQL suite passes | Applied by owner, verified |
| Migration 0014 (internal-table lockdown) | Supabase SQL Editor success; anon `GET` on `system_events` and `transporters` returns `401` | Applied, verified |
| Admin Finance read endpoints | Live admin panel displays the Finance tab; endpoints require admin JWT | Verified earlier |
| Payment → ledger posting | Unit/API tests with faked Flutterwave and RPC only | **Not yet exercised live** |
| Render deployment of this stage | Not deployed at the time of writing | **Pending** |
| Browser E2E / Playwright | Playwright not installed | **Not run** |
| Admin status dropdown / SMS receipt in browser | Code changed; no browser run | **Not yet exercised** |
| COD → ledger posting | Unit/API tests with faked RPC only | **Not yet exercised live** |
| Migration 0016 (settlements) | Not applied to Supabase | **Pending owner action** |
| Payout → settlement journal | Unit/API tests and PGlite only | **Not yet exercised live** |

A live posting can only be claimed after a real verified transaction produces a
`payment:<tx_ref>` journal. The read-only queries in
[`docs/FINANCIAL_LEDGER.md`](FINANCIAL_LEDGER.md) §7 are the acceptance check.

---

## 6. Reproduction prerequisites

* `.test-deps/` contains the Python test dependencies (pytest, requests, Flask,
  cryptography, PyJWT).
* `tests/sql/node_modules/` contains `@electric-sql/pglite 0.5.8`.
* Tests use `FLASK_ENV=development`, a fixed test `JWT_SECRET`, a placeholder
  Supabase URL/key, and faked network clients. No real secret is read or written.
* The SQL tests stub Supabase roles and `auth.uid()`; they do not connect to the
  live project.
