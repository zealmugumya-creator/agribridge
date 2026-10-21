# AgriBridge — Test Results

**Date:** 2026-10-21
**Stage:** Verified customer payments posted to the double-entry ledger
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
318 passed, 1 skipped, 1 warning in 15.66s
```

The skipped test is `tests/e2e/test_consent_ui.py`; it requires Playwright,
which is not installed in this environment. That is a missing browser-test
dependency, not a passing E2E claim.

Targeted payment/ledger run:

```bash
PYTHONPATH=.test-deps python -m pytest tests/test_ledger.py tests/test_payments.py \
  tests/test_card_payments.py tests/test_admin_finance.py -q
```

```
67 passed, 1 warning in 1.31s
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

The ledger suite applies migration 0013, verifies account seeding, balanced and
unbalanced journals, idempotent replay, append-only guards, reversal rules,
admin/anon access, and rollback. It also posts the exact backend payload shape
(two-decimal string amounts, null actor) to prove the app's journal format is
accepted by Postgres. The security suite reproduces the original
anonymous-read vulnerability and proves migration 0014 closes it.

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

## 5. Live verification status

| Item | Evidence | Status |
|---|---|---|
| Migration 0013 (ledger core) | Supabase SQL Editor: "Success. No rows returned"; SQL suite passes | Applied by owner, verified |
| Migration 0014 (internal-table lockdown) | Supabase SQL Editor success; anon `GET` on `system_events` and `transporters` returns `401` | Applied, verified |
| Admin Finance read endpoints | Live admin panel displays the Finance tab; endpoints require admin JWT | Verified earlier |
| Payment → ledger posting | Unit/API tests with faked Flutterwave and RPC only | **Not yet exercised live** |
| Render deployment of this stage | Not deployed at the time of writing | **Pending** |
| Browser E2E / Playwright | Playwright not installed | **Not run** |

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
