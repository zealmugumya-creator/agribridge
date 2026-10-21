# AgriBridge — Financial Ledger

**Last updated:** 2026-10-21
**Migration:** `supabase/migrations/0013_ledger_core.up.sql` (applied to the live project and verified)
**Backend:** `services/ledger.py`, wired into `_flw_verify_and_mark` and
`/api/admin/orders/<id>/confirm-delivery` in `app.py`

This document describes what the ledger records today, the accounting rules it
enforces, how a failed posting is detected, and what is deliberately not yet
implemented. It does not claim that settlements or refunds are live.

---

## 1. What is implemented

| Event | Trigger | Ledger effect | Status |
|---|---|---|---|
| Customer payment received | Flutterwave server-side verification (webhook, direct card, OTP completion) | Debit `PROVIDER_CLEARING`; credit `FARMER_PAYABLE`, `CUSTOMER_REFUND_PAYABLE` and/or `SUSPENSE_OVERPAYMENT` | **Implemented** |
| Admin reads journals / balances | `GET /api/admin/finance/*` | Read-only | **Implemented** |
| Journal reversal | `reverse_journal()` SQL function | Mirror entry + audit row | **Database only** — no admin route yet |
| Farmer / transporter settlement | — | Not posted | **Not implemented** |
| Cash-on-delivery collected | Admin delivery confirmation, order method `cash` | Debit `COD_CASH_HELD`; same credits as a provider receipt | **Implemented** |
| Customer refund execution | — | Refund liability is recognised; money movement is manual | **Not implemented** |

The ledger is the accounting record. The `payouts` table (used by the Finance
tab for settlement status) is a separate operational record and is not yet
created automatically.

---

## 2. Chart of accounts

Seeded once by migration 0013. Amounts are `numeric(18,2)`; accounts do not
carry per-currency sub-accounts, so only UGX may be posted.

| Code | Type | Normal side | Meaning |
|---|---|---|---|
| `PROVIDER_CLEARING` | asset | debit | Money received by the payment provider, not yet settled to the bank |
| `COD_CASH_HELD` | asset | debit | Cash collected on delivery, not yet banked |
| `FARMER_PAYABLE` | liability | credit | Owed to farmers |
| `TRANSPORTER_PAYABLE` | liability | credit | Owed to transporters |
| `CUSTOMER_REFUND_PAYABLE` | liability | credit | Owed back to customers |
| `SUSPENSE_OVERPAYMENT` | liability | credit | Unallocated receipts / overpayments |
| `COMMISSION_REVENUE` | revenue | credit | AgriBridge commission |
| `DELIVERY_REVENUE` | revenue | credit | AgriBridge delivery income |
| `LOGISTICS_EXPENSE` | expense | debit | Logistics cost |

---

## 3. Customer-payment posting rules

Every verified receipt produces exactly one journal:

```
reference:   payment:<checkout_ref>
kind:        customer_payment
payment_ref: <checkout_ref>
actor:       null (system event, not an admin action)
currency:    UGX
```

Lines:

| Account | Side | Amount | When |
|---|---|---|---|
| `PROVIDER_CLEARING` | debit | Amount verified by Flutterwave | Always |
| `FARMER_PAYABLE` | credit | Product value of orders whose status is not `cancelled` | If non-zero |
| `CUSTOMER_REFUND_PAYABLE` | credit | Product value of `cancelled` orders in the same checkout | If non-zero |
| `SUSPENSE_OVERPAYMENT` | credit | Verified amount minus order total | If non-zero |

Zero-amount lines are omitted. The database rejects journals with fewer than two
lines, non-positive amounts, more than two decimals, unknown accounts, or
unbalanced debits/credits.

### Amount source

Order value is computed by `services/finance.py:order_total`, which prefers the
server-generated `total_amount` column (`quantity_kg * price_per_kg`), then the
legacy `total_price`, then quantity × price. The payment floor and the ledger
both use this function. A client-supplied amount can never lower the charge.

### Why commission is not recognised at receipt

`platform_config.commission_pct` applies to **payouts**, and no payout exists at
payment time. The full product value is therefore credited to
`FARMER_PAYABLE`. When a settlement is posted, the commission moves from
`FARMER_PAYABLE` to `COMMISSION_REVENUE` and only the net amount leaves through
`PROVIDER_CLEARING`. Recognising commission at receipt *and* at settlement would
overstate revenue. The posting metadata records
`"commission_policy": "recognized_at_settlement"`.

Current live configuration: `platform_config.commission_pct = 0`. No commission
is invented.

### Cancelled and overpaid checkouts

* A cancelled order in a paid checkout is still money received. It is credited
  to `CUSTOMER_REFUND_PAYABLE`, not to the farmer. The order's
  `payment_status` is still set to `paid` because the money did arrive; a later
  refund must reverse the liability.
* Anything received above the sum of order totals goes to
  `SUSPENSE_OVERPAYMENT`. It is never credited to a farmer or to revenue.
* Underpayment fails verification and posts nothing.

### Cash on delivery

`POST /api/admin/orders/<id>/confirm-delivery` first moves the order to
`delivered` through `set_order_status`, then handles payment:

* Already `paid` → nothing further is posted.
* Method `cash` and unpaid → one journal per checkout:

  ```
  reference:   cod:<checkout_ref>
  kind:        cod_collection
  debit:       COD_CASH_HELD   amount = order total
  credit:      FARMER_PAYABLE / CUSTOMER_REFUND_PAYABLE / SUSPENSE_OVERPAYMENT
  ```

  The order is marked `paid` only after the journal posts. A ledger failure
  returns `503` and leaves the order unpaid so the admin can retry; the retry is
  idempotent. The driver is assumed to have collected exactly the order total.
* Any other unpaid method → the order stays `unpaid` and the response says so.
  Confirming a delivery never invents a gateway payment.

Cash held on delivery is an asset until it is banked; a banking transfer is not
implemented yet.

---

## 4. Idempotency and concurrency

* The journal reference is derived only from the checkout reference:
  `payment:<tx_ref>`. Flutterwave attempt suffixes (`~abc123`) are stripped by
  `_base_ref` before use. COD uses `cod:<ref>` so the two events can never
  collide.
* `ledger_journals.reference` is `UNIQUE`. `post_journal` returns the existing
  journal id and posts nothing when the reference already exists, including
  when two webhook deliveries race.
* A retry after an ambiguous timeout is therefore safe.

---

## 5. Failure handling

Order of operations in `_flw_verify_and_mark`:

1. Verify the transaction with Flutterwave (server-to-server). The webhook body
   alone is never trusted.
2. Require `status = successful`, matching checkout reference, **explicit**
   `currency = UGX`, and `received >= order total`.
3. Mark every order row with that `payment_ref` as `paid`. If this write fails,
   the function returns failure and posts nothing.
4. Post the journal.
5. Send Discord, in-app notification and receipt (best effort).

If step 4 fails:

* The payment is **not** reported as failed. The money was verified and the
  order is paid; telling the buyer otherwise risks a second payment.
* A `payment_ledger_post_failed` row is written to `system_events` and a
  Discord alert (`payment.ledger_failed`) is sent.
* The admin Reconciliation view reports `missing_ledger_entry` for the paid
  order, so the gap cannot disappear silently.

> **Expected after first deploy:** every order paid before the ledger was wired
> will appear as `missing_ledger_entry`. That is a historical gap, not a new
> bug. Do not "fix" it by inventing journals; backfill only from verified
> provider records.

---

## 6. Reconciliation checks

`services/finance.py:build_reconciliation` compares paid orders with active
journals sharing the same `payment_ref` and reports:

| Code | Severity | Meaning |
|---|---|---|
| `missing_ledger_entry` | error | Paid order, no active payment journal |
| `amount_mismatch` | error | Journal total differs from order total |
| `duplicate_ledger_posting` | error | More than one active journal for a reference |
| `payment_reversed_but_order_paid` | warning | Journal reversed, order still `paid` |
| `ledger_payment_without_paid_order` | warning | Customer payment journal with no paid order |
| `paid_order_without_payment_ref` | warning | Cannot be reconciled |
| `refund_without_reversal` | warning | Order marked refunded, no reversal journal |

A journal is "active" when `status = 'posted'` and `kind <> 'reversal'`.

---

## 7. Read-only verification (Supabase SQL Editor)

```sql
-- Account balances: assets/expenses debit-normal, liabilities/revenue credit-normal
select code, name, account_type, normal_side, total_debits, total_credits, balance
from public.ledger_account_balances
order by code;

-- The 20 most recent journals with their totals
select j.reference, j.kind, j.payment_ref, j.status, j.posted_at,
       coalesce(sum(l.amount) filter (where l.side = 'debit'), 0)  as debits,
       coalesce(sum(l.amount) filter (where l.side = 'credit'), 0) as credits
from public.ledger_journals j
left join public.ledger_lines l on l.journal_id = j.id
group by j.id
order by j.posted_at desc
limit 20;

-- Paid payment refs that have no active journal (historical gaps or failed posts)
select o.payment_ref,
       count(*) as paid_orders,
       sum(coalesce(o.total_amount, o.total_price, 0)) as order_total
from public.orders o
where o.payment_status = 'paid'
  and coalesce(o.payment_ref, '') <> ''
  and not exists (
    select 1 from public.ledger_journals j
    where j.payment_ref = o.payment_ref
      and j.status = 'posted'
      and j.kind <> 'reversal'
  )
group by o.payment_ref
order by order_total desc;
```

Expected result for a UGX 50,000 checkout with no commission:

```
PROVIDER_CLEARING  debit  50000.00
FARMER_PAYABLE     credit 50000.00
```

RLS allows admins to read the ledger through an authenticated admin session;
the SQL Editor runs with the service role.

---

## 8. Deliberately not implemented yet

* Automatic creation of `payouts` rows and settlement journals.
* Farmer and transporter payout execution and postings.
* Commission recognition and its configuration rollout.
* Banking transfers from `COD_CASH_HELD` and `PROVIDER_CLEARING` to the bank.
* A backend route to call `reverse_journal` for refunds and disputes.
* Transporter agreed charge and delivery fee capture (needed before
  `TRANSPORTER_PAYABLE` / `DELIVERY_REVENUE` can hold real money).

No dashboard figure is fabricated while these are missing: the Finance API
returns `available: false` with a reason for transporter payables and delivery
revenue.
