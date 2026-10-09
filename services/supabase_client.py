# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Supabase data access: PostgREST helpers, RPC calls, and a real health probe.

Audit findings addressed:
  * M6 — `/health` must verify actual DB connectivity, not just "key is set".
  * C1/C2 — order creation goes through the `create_order_atomic` Postgres RPC so
    price recomputation and stock reservation happen server-side, atomically.
  * H3 — the notification outbox is drained via the `claim_notifications` /
    `resolve_notification` RPCs (durable, idempotent).

All calls use the SERVICE-ROLE key and are backend-only. Bounded timeouts on
every request (Phase 10). This module never raises on transient failures where a
soft fallback is safer; it returns a structured result the caller can branch on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from services.logging import get_logger

log = get_logger(__name__)

_TIMEOUT = 8


@dataclass
class RpcResult:
    ok: bool
    data: Any = None
    status: int = 0
    error: str = ""


class SupabaseClient:
    def __init__(self, url: str, service_key: str):
        self._url = (url or "").rstrip("/")
        self._key = service_key or ""

    @property
    def configured(self) -> bool:
        return bool(self._url and self._key)

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        h = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    # ── Health probe: a real round-trip, not a key-presence check (M6) ────────
    def ping(self) -> RpcResult:
        """Execute a trivial query through PostgREST to prove connectivity."""
        if not self.configured:
            return RpcResult(ok=False, error="supabase not configured")
        try:
            # HEAD on a system-visible table; 2xx/401/406 all prove we reached the
            # DB gateway. A connection error / timeout does not.
            resp = requests.get(
                f"{self._url}/rest/v1/platform_config",
                params={"select": "commission_pct", "limit": "1"},
                headers=self._headers(),
                timeout=_TIMEOUT,
            )
            reachable = resp.status_code < 500
            return RpcResult(ok=reachable, status=resp.status_code,
                             error="" if reachable else f"http {resp.status_code}")
        except requests.RequestException as exc:
            return RpcResult(ok=False, error=type(exc).__name__)

    # ── Generic table access (read paths used by admin/public endpoints) ──────
    def select(self, table: str, filters: dict[str, Any] | None = None,
               limit: int = 100) -> list[dict]:
        if not self.configured:
            return []
        params: dict[str, Any] = {"limit": limit}
        if filters:
            params.update(filters)
        try:
            resp = requests.get(f"{self._url}/rest/v1/{table}",
                                params=params, headers=self._headers(), timeout=_TIMEOUT)
            return resp.json() if resp.ok else []
        except (requests.RequestException, ValueError) as exc:
            log.error("supabase.select_failed", table=table, error=type(exc).__name__)
            return []

    def update(self, table: str, data: dict, eq_col: str, eq_val: Any) -> bool:
        if not self.configured:
            return False
        try:
            resp = requests.patch(f"{self._url}/rest/v1/{table}", json=data,
                                  params={eq_col: f"eq.{eq_val}"},
                                  headers=self._headers("return=minimal"), timeout=_TIMEOUT)
            return resp.status_code < 300
        except requests.RequestException as exc:
            log.error("supabase.update_failed", table=table, error=type(exc).__name__)
            return False

    def upsert(self, table: str, data: dict | list[dict],
               on_conflict: str | None = None) -> RpcResult:
        """Insert-or-update by natural key (PostgREST merge resolution).

        Used for the admin device-token registry: a token refresh upserts on
        (provider, token) rather than creating duplicates.
        """
        if not self.configured:
            return RpcResult(ok=False, error="supabase not configured")
        params: dict[str, Any] = {}
        if on_conflict:
            params["on_conflict"] = on_conflict
        try:
            resp = requests.post(
                f"{self._url}/rest/v1/{table}", json=data, params=params,
                headers=self._headers("return=representation,resolution=merge-duplicates"),
                timeout=_TIMEOUT)
            if resp.status_code < 300:
                try:
                    return RpcResult(ok=True, data=resp.json(), status=resp.status_code)
                except ValueError:
                    return RpcResult(ok=True, data=None, status=resp.status_code)
            return RpcResult(ok=False, status=resp.status_code, error=resp.text[:300])
        except requests.RequestException as exc:
            log.error("supabase.upsert_failed", table=table, error=type(exc).__name__)
            return RpcResult(ok=False, error=type(exc).__name__)

    def delete(self, table: str, filters: dict[str, Any]) -> bool:
        if not self.configured:
            return False
        try:
            resp = requests.delete(f"{self._url}/rest/v1/{table}", params=filters,
                                   headers=self._headers("return=minimal"), timeout=_TIMEOUT)
            return resp.status_code < 300
        except requests.RequestException as exc:
            log.error("supabase.delete_failed", table=table, error=type(exc).__name__)
            return False

    # ── RPC calls (server-side Postgres functions from the migrations) ────────
    def rpc(self, fn: str, args: dict[str, Any]) -> RpcResult:
        if not self.configured:
            return RpcResult(ok=False, error="supabase not configured")
        try:
            resp = requests.post(f"{self._url}/rest/v1/rpc/{fn}", json=args,
                                 headers=self._headers("return=representation"),
                                 timeout=_TIMEOUT)
            if resp.ok:
                try:
                    return RpcResult(ok=True, data=resp.json(), status=resp.status_code)
                except ValueError:
                    return RpcResult(ok=True, data=None, status=resp.status_code)
            # Surface the Postgres error message for logging (may contain the
            # raised exception text from our RPCs). Never contains secrets.
            detail = ""
            try:
                detail = str(resp.json().get("message", ""))[:300]
            except ValueError:
                detail = resp.text[:300]
            return RpcResult(ok=False, status=resp.status_code, error=detail or f"http {resp.status_code}")
        except requests.RequestException as exc:
            return RpcResult(ok=False, error=type(exc).__name__)
