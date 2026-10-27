# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Admin dashboard KPI counts.

`/api/admin/stats` used to download up to five full tables (limit=1000, every
column) purely to call len() on them, and a failed read became a silent zero.
These tests pin the replacement contract: exact counts from Content-Range, a
failure reported as unavailable rather than 0, and revenue that admits when it is
only a lower bound.
"""
from __future__ import annotations

import pytest

import app as app_mod


@pytest.fixture
def client():
    app_mod.app.config['TESTING'] = True
    with app_mod.app.test_client() as c:
        yield c


def _admin_headers():
    return {'Authorization': f'Bearer {app_mod.make_token({"sub": "admin", "role": "admin"})}'}


class _Resp:
    def __init__(self, total, ok=True, status=200, body=None):
        self.headers = {'Content-Range': f'*/{total}'} if total is not None else {}
        self.ok = ok
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def test_supa_count_reads_the_total_from_content_range(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        return _Resp('84')

    monkeypatch.setattr(app_mod.requests, 'get', fake_get)
    monkeypatch.setattr(app_mod, 'SUPABASE_KEY', 'k')
    n, err = app_mod.supa_count('farmers')
    assert (n, err) == (84, None)
    url, params, headers = calls[0]
    assert url.endswith('/rest/v1/farmers')
    assert params['limit'] == 0, 'a count must never ask for rows'
    assert headers['Prefer'] == 'count=exact'


@pytest.mark.parametrize('header', ['*/*', '*/~120', ''])
def test_supa_count_refuses_an_inexact_total(monkeypatch, header):
    def fake_get(url, params=None, headers=None, timeout=None):
        r = _Resp(None)
        r.headers = {'Content-Range': header} if header else {}
        return r

    monkeypatch.setattr(app_mod.requests, 'get', fake_get)
    monkeypatch.setattr(app_mod, 'SUPABASE_KEY', 'k')
    n, err = app_mod.supa_count('orders')
    assert n is None and err, 'an unusable total must be an error, not 0'


def test_supa_count_reports_a_denied_table(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp(None, ok=False, status=401,
                     body={'code': '42501', 'message': 'permission denied'})

    monkeypatch.setattr(app_mod.requests, 'get', fake_get)
    monkeypatch.setattr(app_mod, 'SUPABASE_KEY', 'k')
    n, err = app_mod.supa_count('transporters')
    assert n is None and '42501' in err


@pytest.fixture
def counts(monkeypatch):
    """Stub supa_count by filter signature and record what was asked for."""
    asked = []
    table = {
        (): 84,
        ('is_available=eq.true',): 63,
        ('status=eq.active',): 5,
        ('status=eq.pending',): 9,
        ('payment_status=eq.paid',): 27,
        ('is_verified=eq.true',): 40,
    }

    def fake(table_name, filters=None):
        key = tuple(sorted(f'{k}={v}' for k, v in (filters or {}).items() if k != 'limit'))
        asked.append((table_name, key))
        return table.get(key, 0), None

    monkeypatch.setattr(app_mod, 'supa_count', fake)
    return asked


def test_stats_uses_counts_and_never_pages_rows(client, monkeypatch, counts):
    pages = []
    monkeypatch.setattr(app_mod, 'supa_get',
                        lambda *a, **k: pages.append(a) or [])
    monkeypatch.setattr(app_mod, 'supa_get_checked',
                        lambda t, f=None, limit=100: ([{'total_price': 50000}] * 3, None))
    r = client.get('/api/admin/stats', headers=_admin_headers())
    assert r.status_code == 200
    body = r.get_json()
    assert body['farmers'] == 84 and body['transporters'] == 5
    assert body['orders_pending'] == 9 and body['orders_paid'] == 27
    assert body['listings_live'] == 63 and body['farmers_verified'] == 40
    assert pages == [], 'the KPI route must not download table pages any more'
    assert body['errors'] == {}


def test_stats_keeps_a_failed_count_as_unavailable(client, monkeypatch):
    def fake(table_name, filters=None):
        if table_name == 'animal_listings':
            return None, '42501: permission denied'
        return 12, None

    monkeypatch.setattr(app_mod, 'supa_count', fake)
    monkeypatch.setattr(app_mod, 'supa_get_checked', lambda t, f=None, limit=100: ([], None))
    body = client.get('/api/admin/stats', headers=_admin_headers()).get_json()
    assert body['animals'] is None, 'a failed query must not be shown as zero'
    assert 'animals' in body['errors']


def test_stats_revenue_is_paid_orders_only(client, monkeypatch):
    seen = {}

    def checked(table, filters=None, limit=100):
        seen.update(filters or {})
        return [{'total_price': 120000}, {'total_price': 80000}], None

    monkeypatch.setattr(app_mod, 'supa_count', lambda t, f=None: (2, None))
    monkeypatch.setattr(app_mod, 'supa_get_checked', checked)
    body = client.get('/api/admin/stats', headers=_admin_headers()).get_json()
    assert body['revenue_ugx'] == 200000
    assert seen['payment_status'] == 'eq.paid', 'unpaid orders are not revenue'
    assert seen['select'] == 'total_price', 'only the summed column may be fetched'
    assert body['revenue_partial'] is False


def test_stats_flags_revenue_when_the_cap_was_reached(client, monkeypatch):
    monkeypatch.setattr(app_mod, 'supa_count',
                        lambda t, f=None: ((5000, None) if (f or {}).get('payment_status')
                                           else (10, None)))
    monkeypatch.setattr(app_mod, 'supa_get_checked',
                        lambda t, f=None, limit=100: ([{'total_price': 1000}] * limit, None))
    body = client.get('/api/admin/stats', headers=_admin_headers()).get_json()
    assert body['revenue_partial'] is True, 'a capped sum is a lower bound, not the total'


def test_stats_revenue_failure_is_reported_not_zeroed(client, monkeypatch):
    monkeypatch.setattr(app_mod, 'supa_count', lambda t, f=None: (4, None))
    monkeypatch.setattr(app_mod, 'supa_get_checked',
                        lambda t, f=None, limit=100: (None, '503: upstream unavailable'))
    body = client.get('/api/admin/stats', headers=_admin_headers()).get_json()
    assert body['revenue_ugx'] is None
    assert 'revenue_ugx' in body['errors']


def test_stats_requires_an_admin_token(client):
    assert client.get('/api/admin/stats').status_code == 401
    user = {'Authorization': f'Bearer {app_mod.make_token({"sub": "u", "role": "farmer"})}'}
    assert client.get('/api/admin/stats', headers=user).status_code == 403
