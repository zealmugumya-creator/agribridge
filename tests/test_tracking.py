# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Tracking lookup: an order is trackable the moment it exists (before any rider is
assigned), and only non-personal fields are returned."""
from __future__ import annotations

import pytest

import app as app_mod


@pytest.fixture
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


def _db(monkeypatch, orders=(), deliveries=(), farmers=()):
    def fake(table, filters=None, limit=100):
        rows = {"orders": orders, "deliveries": deliveries, "farmers": farmers}[table]
        out = []
        for r in rows:
            if all(str(r.get(k)) == v[3:] for k, v in (filters or {}).items() if v.startswith("eq.")):
                out.append(r)
        return out
    monkeypatch.setattr(app_mod, "supa_get", fake)


ORDER = {"id": "o1", "tracking_code": "TRK-ABC1234567", "order_ref": "AB-20260101-AAAA1111",
         "status": "confirmed", "delivery_address": "Nakasero Market, Kampala",
         "farmer_id": "f1", "buyer_id": "secret-buyer", "buyer_phone": "0771000000",
         "created_at": "2026-01-01T10:00:00Z"}


def test_fresh_order_is_found_without_a_delivery_row(client, monkeypatch):
    _db(monkeypatch, orders=[ORDER], farmers=[{"id": "f1", "district": "Mbarara"}])
    r = client.get("/api/track/trk-abc1234567")          # case-insensitive
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert j["pickup_location"] == "Mbarara"
    assert j["delivery_location"] == "Nakasero Market, Kampala"
    assert j["status"] == "scheduled" and j["rider_assigned"] is False


def test_lookup_by_order_ref(client, monkeypatch):
    _db(monkeypatch, orders=[ORDER])
    assert client.get("/api/track/AB-20260101-AAAA1111").get_json()["tracking_code"] == "TRK-ABC1234567"


def test_delivery_row_overrides_status_and_shows_rider(client, monkeypatch):
    _db(monkeypatch, orders=[ORDER],
        deliveries=[{"order_id": "o1", "status": "in_transit", "driver_name": "Moses",
                     "driver_phone": "0700", "pickup_location": "Masaka"}])
    j = client.get("/api/track/TRK-ABC1234567").get_json()
    assert j["status"] == "in_transit" and j["driver_name"] == "Moses" and j["pickup_location"] == "Masaka"
    assert j["rider_assigned"] is True


def test_no_personal_data_leaks(client, monkeypatch):
    _db(monkeypatch, orders=[ORDER])
    body = client.get("/api/track/TRK-ABC1234567").get_data(as_text=True)
    for secret in ("secret-buyer", "0771000000", "driver_phone", "buyer_id"):
        assert secret not in body


def test_unknown_and_malformed_codes(client, monkeypatch):
    _db(monkeypatch)
    assert client.get("/api/track/TRK-NOPE000000").status_code == 404
    assert client.get("/api/track/a%27%20or%201=1").status_code == 400


def test_standalone_delivery_still_trackable(client, monkeypatch):
    _db(monkeypatch, deliveries=[{"tracking_code": "DEL-2026-001", "status": "delivered",
                                  "pickup_location": "Gulu", "delivery_location": "Kampala"}])
    j = client.get("/api/track/del-2026-001").get_json()
    assert j["status"] == "delivered"
