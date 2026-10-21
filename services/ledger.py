# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Post verified customer payments into the double-entry ledger.

Accounting policy for a receipt:

  debit  PROVIDER_CLEARING          money received from the provider
  credit FARMER_PAYABLE             product value of live orders
  credit CUSTOMER_REFUND_PAYABLE    product value of cancelled orders in the batch
  credit SUSPENSE_OVERPAYMENT       anything received above the order total

Commission is deliberately NOT recognised here. `platform_config.commission_pct`
applies to future payouts and no payout exists at receipt time; the settlement
entry will move the commission out of FARMER_PAYABLE into COMMISSION_REVENUE.
Recognising it twice would overstate revenue.

Everything up to the RPC call is pure arithmetic so the money rules can be
tested offline. The database enforces balance, append-only and idempotency
(`payment:<tx_ref>`), so a retried webhook cannot post the same receipt twice.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from services import finance
from services.logging import get_logger

log = get_logger(__name__)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
UGX = "UGX"

ACCOUNT_PROVIDER_CLEARING = "PROVIDER_CLEARING"
ACCOUNT_FARMER_PAYABLE = "FARMER_PAYABLE"
ACCOUNT_CUSTOMER_REFUND_PAYABLE = "CUSTOMER_REFUND_PAYABLE"
ACCOUNT_SUSPENSE_OVERPAYMENT = "SUSPENSE_OVERPAYMENT"

CANCELLED_STATUSES = {"cancelled"}


class LedgerError(ValueError):
    """Raised when a receipt cannot be expressed as a balanced journal."""


@dataclass
class PaymentPosting:
    ok: bool
    reference: str
    journal_id: str | None = None
    error: str = ""
    lines: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def money(value) -> Decimal:
    """Return an exact 2-decimal amount.

    Floats are converted through their string form so 0.1 + 0.2 style drift
    never reaches a journal. Non-finite and non-numeric input is rejected.
    """
    if value is None or value == "":
        raise LedgerError("amount is required")
    if isinstance(value, bool):
        raise LedgerError(f"invalid amount: {value!r}")
    try:
        if isinstance(value, float):
            if not math.isfinite(value):
                raise LedgerError(f"invalid amount: {value!r}")
            amount = Decimal(str(value))
        else:
            amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise LedgerError(f"invalid amount: {value!r}") from None
    if not amount.is_finite():
        raise LedgerError(f"invalid amount: {value!r}")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def whole(value) -> int:
    """Round an amount to the nearest whole currency unit."""
    return int(money(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fmt(value: Decimal) -> str:
    return f"{value:.2f}"


def _line(account: str, side: str, amount: Decimal, memo: str) -> dict:
    return {"account": account, "side": side, "amount": _fmt(amount), "memo": memo}


def build_payment_lines(orders: list[dict], received, currency: str = UGX) -> tuple[list[dict], dict]:
    """Build the journal lines for one verified receipt.

    `orders` are the order rows sharing the checkout's payment_ref. Cancelled
    orders are still part of the money received, but that value is owed back to
    the customer rather than to the farmer.
    """
    cur = str(currency or "").strip().upper()
    if cur != UGX:
        raise LedgerError(f"unsupported ledger currency: {currency!r}")

    received_amount = money(received)
    if received_amount <= ZERO:
        raise LedgerError("received amount must be positive")

    farmer_total = ZERO
    refund_total = ZERO
    order_ids: list[str] = []
    cancelled_ids: list[str] = []

    for order in orders or []:
        if not isinstance(order, dict):
            raise LedgerError("order rows must be objects")
        total = money(finance.order_total(order))
        if total < ZERO:
            raise LedgerError("order total cannot be negative")
        if total == ZERO:
            continue
        order_id = str(order.get("id") or "")
        if order_id:
            order_ids.append(order_id)
        if str(order.get("status") or "").strip().lower() in CANCELLED_STATUSES:
            refund_total += total
            if order_id:
                cancelled_ids.append(order_id)
        else:
            farmer_total += total

    orders_total = farmer_total + refund_total
    if orders_total <= ZERO:
        raise LedgerError("checkout has no positive order total")
    if received_amount < orders_total:
        raise LedgerError(
            f"received {_fmt(received_amount)} is less than order total {_fmt(orders_total)}"
        )
    suspense = received_amount - orders_total

    lines = [_line(ACCOUNT_PROVIDER_CLEARING, "debit", received_amount,
                   "customer payment received")]
    if farmer_total > ZERO:
        lines.append(_line(ACCOUNT_FARMER_PAYABLE, "credit", farmer_total,
                           "product value owed to farmers"))
    if refund_total > ZERO:
        lines.append(_line(ACCOUNT_CUSTOMER_REFUND_PAYABLE, "credit", refund_total,
                           "cancelled order refund due"))
    if suspense > ZERO:
        lines.append(_line(ACCOUNT_SUSPENSE_OVERPAYMENT, "credit", suspense,
                           "received above order total"))

    debits = sum((money(line["amount"]) for line in lines if line["side"] == "debit"), ZERO)
    credits = sum((money(line["amount"]) for line in lines if line["side"] == "credit"), ZERO)
    if debits != credits or len(lines) < 2:
        raise LedgerError("journal does not balance")

    summary = {
        "currency": cur,
        "received": received_amount,
        "orders_total": orders_total,
        "farmer_payable": farmer_total,
        "refund_payable": refund_total,
        "suspense": suspense,
        "order_ids": order_ids,
        "cancelled_order_ids": cancelled_ids,
        "order_count": len(order_ids),
    }
    return lines, summary


def post_verified_payment(db, *, tx_ref: str, orders: list[dict], received,
                          currency: str = UGX, provider: str = "",
                          flw_id=None, actor=None) -> PaymentPosting:
    """Post one idempotent customer-payment journal through `post_journal`.

    `db` is a SupabaseClient-like object with `.rpc(name, args)`. The reference
    is derived from the checkout ref, so retries return the original journal.
    """
    tx_ref = str(tx_ref or "").strip()
    reference = f"payment:{tx_ref}"
    if not tx_ref or len(tx_ref) > 160:
        return PaymentPosting(ok=False, reference=reference,
                              error="a valid payment reference is required")

    try:
        lines, summary = build_payment_lines(orders, received, currency)
    except LedgerError as exc:
        return PaymentPosting(ok=False, reference=reference, error=str(exc))

    metadata = {
        "provider": str(provider or "")[:80],
        "flw_id": None if flw_id is None else str(flw_id)[:80],
        "currency": summary["currency"],
        "received": _fmt(summary["received"]),
        "orders_total": _fmt(summary["orders_total"]),
        "farmer_payable": _fmt(summary["farmer_payable"]),
        "refund_payable": _fmt(summary["refund_payable"]),
        "suspense": _fmt(summary["suspense"]),
        "order_ids": summary["order_ids"],
        "cancelled_order_ids": summary["cancelled_order_ids"],
        "commission_policy": "recognized_at_settlement",
    }
    args = {
        "p_reference": reference,
        "p_kind": "customer_payment",
        "p_description": f"Customer payment {tx_ref}" + (f" via {provider}" if provider else ""),
        "p_lines": lines,
        "p_payment_ref": tx_ref,
        "p_actor": actor,
        "p_metadata": metadata,
        "p_currency": summary["currency"],
    }

    try:
        result = db.rpc("post_journal", args)
    except Exception as exc:  # a transport failure must not crash the payment path
        log.error("ledger.post_exception", reference=reference, error=type(exc).__name__)
        return PaymentPosting(ok=False, reference=reference, lines=lines, summary=summary,
                              error=type(exc).__name__)

    if not getattr(result, "ok", False):
        error = str(getattr(result, "error", "") or "ledger post failed")[:300]
        log.error("ledger.post_failed", reference=reference, error=error)
        return PaymentPosting(ok=False, reference=reference, lines=lines, summary=summary,
                              error=error)

    data = getattr(result, "data", None)
    if isinstance(data, list) and data:
        data = data[0]
    journal_id = str(data) if isinstance(data, str) and data else None
    if not journal_id:
        log.error("ledger.post_without_id", reference=reference)
        return PaymentPosting(ok=False, reference=reference, lines=lines, summary=summary,
                              error="post_journal returned no journal id")

    log.info("ledger.posted", reference=reference, journal_id=journal_id,
             amount=_fmt(summary["received"]))
    return PaymentPosting(ok=True, reference=reference, journal_id=journal_id,
                          lines=lines, summary=summary)
