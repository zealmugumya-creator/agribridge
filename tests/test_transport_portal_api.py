# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Transporter portal API surface.

These tests pin the behaviour the transporter brief asks for and that a mocked
screen cannot prove: the caller's identity comes from the verified session (not
the body), a lost claim or a lost status race is reported as lost, a fee is never
written from the client side, a counter-offer is not money, earnings never
invent a withdrawable balance, and confirming a delivery that has an issued
recipient code requires a matching proof.

Postgres owns the exclusivity and proof decisions (0020/0023, covered in
tests/sql/transport_assignment.test.mjs). What is checked here is that the HTTP
layer asks Postgres the right question and reports every answer honestly.
"""
from __future__ import annotations

import hashlib
import io

import pytest

import app as app_mod
from services.supabase_client import RpcResult

T1 = {
    'id': 't1', 'supabase_uid': 'u1', 'full_name': 'Driver One',
    'phone': '+256700000000', 'vehicle_type': 'lorry', 'plate_number': 'UAA 001A',
    'district': 'Kampala', 'capacity_kg': 8000, 'status': 'active',
    'is_available': True,
}
ORDER_ID = '0d1e2f3a-0000-4000-8000-000000000001'
DELIVERY_ID = '0d1e2f3a-0000-4000-8000-0000000000d1'


@pytest.fixture
def client():
    app_mod.app.config['TESTING'] = True
    with app_mod.app.test_client() as c:
        yield c


@pytest.fixture
def as_transporter(monkeypatch):
    monkeypatch.setattr(app_mod, 'verify_supabase_user', lambda: ('u1', None))
    monkeypatch.setattr(app_mod, '_log_system_event', lambda *a, **k: None)
    monkeypatch.setattr(app_mod, '_discord_notify', lambda *a, **k: None)
    monkeypatch.setattr(app_mod.NOTIFY, 'enqueue', lambda **k: None)


def _admin_headers():
    token = app_mod.make_token({'sub': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {token}'}


def _db(monkeypatch, *, transporters=None, deliveries=None, orders=None, farmers=None):
    """Route supa_get by table, so a test can see what each call was given."""
    catalog = {'transporters': transporters or [], 'deliveries': deliveries or [],
               'orders': orders or [], 'farmers': farmers or []}

    def fake_get(table, filters=None, limit=100):
        value = catalog.get(table, [])
        if callable(value):
            return value(filters)
        return value

    monkeypatch.setattr(app_mod, 'supa_get', fake_get)


# ── Identity ────────────────────────────────────────────────────────────────
def test_a_login_without_a_transporter_row_is_told_so(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[])
    r = client.get('/api/transporter/jobs')
    assert r.status_code == 404
    assert r.get_json()['code'] == 'transporter_not_linked'
    assert 'not linked' in r.get_json()['error']


def test_identity_comes_from_the_session_not_the_request_body(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1], deliveries=[])
    seen = {}

    def fake_rpc(fn, args):
        seen['fn'], seen['args'] = fn, args
        return RpcResult(ok=True, data={'claimable': True})

    monkeypatch.setattr(app_mod.SUPA, 'rpc', fake_rpc)
    r = client.post(f'/api/transporter/jobs/{DELIVERY_ID}/claim',
                    json={'transporter_id': 't2', 'fee_ugx': 999})
    assert r.status_code == 200
    assert seen['fn'] == 'claim_delivery'
    # The body's transporter id and fee are ignored: only the session identity
    # and the stored fee may reach the claim function.
    assert seen['args'] == {'p_delivery_id': DELIVERY_ID, 'p_transporter_id': 't1'}


def test_an_inactive_account_cannot_claim(client, monkeypatch, as_transporter):
    inactive = {**T1, 'status': 'inactive'}
    _db(monkeypatch, transporters=[inactive])
    monkeypatch.setattr(app_mod.SUPA, 'rpc',
                        lambda *a, **k: pytest.fail('inactive accounts must not reach the RPC'))
    r = client.post(f'/api/transporter/jobs/{DELIVERY_ID}/claim')
    assert r.status_code == 403
    assert r.get_json()['code'] == 'account_inactive'


# ── Job lists ───────────────────────────────────────────────────────────────
def test_open_jobs_do_not_leak_counterparty_contacts(client, monkeypatch, as_transporter):
    order = {'id': ORDER_ID, 'farmer_id': 'f1', 'buyer_id': 'b1',
             'item_category': 'Maize', 'quantity_kg': 500,
             'delivery_address': 'Nakawa', 'status': 'pending'}
    _db(monkeypatch, transporters=[T1], orders=[order], deliveries=[{
        'id': DELIVERY_ID, 'order_id': ORDER_ID, 'pickup_location': 'Jinja',
        'delivery_location': 'Nakawa', 'status': 'scheduled',
        'transport_fee_ugx': None, 'created_at': '2026-10-27T08:00:00Z',
    }])
    body = client.get('/api/transporter/jobs').get_json()
    assert body['count'] == 1
    job = body['jobs'][0]
    for leaked in ('contact_name', 'contact_phone', 'farmer_id', 'buyer_id'):
        assert leaked not in job
    # An unrecorded fee is reported as unrecorded, never as zero.
    assert job['fee_ugx'] is None and job['fee_state'] == 'not_set'


def test_assigned_jobs_include_the_farmer_contact(client, monkeypatch, as_transporter):
    order = {'id': ORDER_ID, 'farmer_id': 'f1', 'item_category': 'Maize',
             'quantity_kg': 500, 'status': 'in_transit'}
    _db(monkeypatch, transporters=[T1], orders=[order],
        farmers=[{'id': 'f1', 'full_name': 'Farmer One', 'phone': '+256711111111'}],
        deliveries=[{'id': DELIVERY_ID, 'order_id': ORDER_ID, 'status': 'in_transit',
                     'transport_fee_ugx': 45000, 'fee_basis': 'admin_set',
                     'offer_status': 'none'}])
    body = client.get('/api/transporter/deliveries').get_json()
    job = body['deliveries'][0]
    assert job['contact_name'] == 'Farmer One'
    assert job['contact_phone'] == '+256711111111'
    assert job['fee_ugx'] == 45000 and job['fee_state'] == 'agreed'
    assert job['next_statuses'] == ['at_checkpoint']


# ── Claims and status ───────────────────────────────────────────────────────
def test_a_lost_claim_is_reported_as_lost(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    monkeypatch.setattr(app_mod.SUPA, 'rpc', lambda fn, args: RpcResult(
        ok=True, data={'claimable': False, 'reason': 'already_claimed'}))
    r = client.post(f'/api/transporter/jobs/{DELIVERY_ID}/claim')
    assert r.status_code == 409
    body = r.get_json()
    assert body['ok'] is False and body['reason'] == 'already_claimed'


def test_an_illegal_status_step_is_refused_with_the_real_state(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1], deliveries=[{
        'id': DELIVERY_ID, 'transporter_id': 't1', 'status': 'in_transit'}])
    monkeypatch.setattr(app_mod.SUPA, 'update_checked',
                        lambda *a, **k: pytest.fail('an illegal step must not write'))
    r = client.post(f'/api/transporter/deliveries/{DELIVERY_ID}/status',
                    json={'status': 'delivered'})
    assert r.status_code == 409
    body = r.get_json()
    assert body['code'] == 'illegal_transition'
    assert body['current_status'] == 'in_transit'
    assert body['allowed'] == ['at_checkpoint']


def test_a_status_write_that_lost_the_race_says_so(client, monkeypatch, as_transporter):
    rows = [{'id': DELIVERY_ID, 'transporter_id': 't1', 'status': 'picked_up'}]

    def fake_get(table, filters=None, limit=100):
        if table != 'deliveries':
            return [T1]
        if (filters or {}).get('select') == 'status':
            return [{'status': 'in_transit'}]
        return rows

    monkeypatch.setattr(app_mod, 'supa_get', fake_get)
    monkeypatch.setattr(app_mod.SUPA, 'update_checked',
                        lambda *a, **k: RpcResult(ok=True, data=[]))
    r = client.post(f'/api/transporter/deliveries/{DELIVERY_ID}/status',
                    json={'status': 'in_transit'})
    assert r.status_code == 409
    body = r.get_json()
    assert body['reason'] == 'changed_elsewhere'
    assert body['current_status'] == 'in_transit'


def test_someone_elses_delivery_is_a_403(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1], deliveries=[])
    r = client.post(f'/api/transporter/deliveries/{DELIVERY_ID}/status',
                    json={'status': 'picked_up'})
    assert r.status_code == 403


# ── Money ───────────────────────────────────────────────────────────────────
def test_a_counter_offer_never_writes_the_agreed_fee(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1], deliveries=[{
        'id': DELIVERY_ID, 'transporter_id': 't1', 'status': 'in_transit',
        'offer_status': 'none'}])
    writes = []

    def fake_update(table, data, filters):
        writes.append((table, data))
        return RpcResult(ok=True, data=[{'id': DELIVERY_ID}])

    monkeypatch.setattr(app_mod.SUPA, 'update_checked', fake_update)
    r = client.post(f'/api/transporter/deliveries/{DELIVERY_ID}/offer',
                    json={'amount_ugx': 60000})
    assert r.status_code == 200
    fields = writes[0][1]
    assert fields['transporter_offer_ugx'] == 60000
    assert fields['offer_status'] == 'proposed'
    assert 'transport_fee_ugx' not in fields


def test_a_pending_offer_cannot_be_replaced(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1], deliveries=[{
        'id': DELIVERY_ID, 'transporter_id': 't1', 'status': 'in_transit',
        'offer_status': 'proposed'}])
    r = client.post(f'/api/transporter/deliveries/{DELIVERY_ID}/offer',
                    json={'amount_ugx': 60000})
    assert r.status_code == 409 and r.get_json()['code'] == 'offer_pending'


def test_earnings_do_not_invent_a_balance(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1], deliveries=[
        {'id': 'd1', 'status': 'delivered', 'transport_fee_ugx': 45000,
         'fee_basis': 'admin_set', 'offer_status': 'none', 'proof_verified_at': '2026-10-27T09:00:00Z'},
        {'id': 'd2', 'status': 'in_transit', 'transport_fee_ugx': 30000,
         'fee_basis': 'admin_set', 'offer_status': 'none'},
        {'id': 'd3', 'status': 'delivered', 'transport_fee_ugx': None,
         'offer_status': 'none'},
    ])
    body = client.get('/api/transporter/earnings').get_json()
    assert body['payment_tracking'] == 'not_available'
    assert 'withdrawable' not in str(body).lower()
    assert body['fee_agreed_total_ugx'] == 75000     # the two recorded fees
    assert body['fee_completed_total_ugx'] == 45000  # only the delivered one
    assert body['jobs_completed'] == 2 and body['jobs_in_progress'] == 1
    assert body['jobs'][0]['admin_confirmed'] is True


# ── Profile ─────────────────────────────────────────────────────────────────
def test_safety_columns_need_an_admin(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    r = client.patch('/api/transporter/profile',
                     json={'vehicle_type': 'lorry', 'capacity_kg': 90000})
    assert r.status_code == 403
    assert r.get_json()['code'] == 'field_requires_admin'
    assert r.get_json()['fields'] == ['capacity_kg', 'vehicle_type']


def test_profile_updates_contact_fields_only(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    writes = []
    monkeypatch.setattr(app_mod.SUPA, 'update_checked', lambda table, data, filters: (
        writes.append(data) or RpcResult(ok=True, data=[{'id': 't1'}])))
    r = client.patch('/api/transporter/profile',
                     json={'phone': '+256712345678', 'district': 'Mbale'})
    assert r.status_code == 200
    assert writes[0] == {'phone': '+256712345678', 'district': 'Mbale'}


def test_a_wrong_phone_shape_is_refused(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    r = client.patch('/api/transporter/profile', json={'phone': '12345'})
    assert r.status_code == 400


# ── Dashboard ───────────────────────────────────────────────────────────────
def test_a_count_that_failed_is_reported_as_unverified(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    answers = {'eq.scheduled': None, 'eq.delivered': 4}

    def fake_count(table, filters):
        return answers.get(filters.get('status'), 2), None

    monkeypatch.setattr(app_mod, 'supa_count', fake_count)
    body = client.get('/api/transporter/dashboard').get_json()
    assert body['metrics']['assigned'] is None
    assert 'assigned' in body['counts_unverified']
    assert body['metrics']['completed'] == 4


# ── Proof of delivery ───────────────────────────────────────────────────────
def _proof_post(client, code='123456', content=b'\xff\xd8\xff\xe0fake-jpeg'):
    return client.post(
        f'/api/transporter/deliveries/{DELIVERY_ID}/proof',
        data={'code': code, 'photo': (io.BytesIO(content), 'door.jpg', 'image/jpeg')},
        content_type='multipart/form-data')


def test_a_matching_code_is_recorded_with_the_photo(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    calls = {}

    def fake_upload(bucket, path, blob, content_type):
        calls['upload'] = (bucket, path, content_type, len(blob))
        return RpcResult(ok=True, data={'bucket': bucket, 'path': path})

    def fake_rpc(fn, args):
        calls['rpc'] = (fn, args)
        return RpcResult(ok=True, data={'ok': True, 'code_matched': True,
                                        'attempts_remaining': 5,
                                        'submitted_at': '2026-10-27T10:00:00Z'})

    monkeypatch.setattr(app_mod.SUPA, 'upload_private_media', fake_upload)
    monkeypatch.setattr(app_mod.SUPA, 'rpc', fake_rpc)
    r = _proof_post(client)
    assert r.status_code == 200
    body = r.get_json()
    assert body['code_matched'] is True and body['submitted'] is True
    assert 'administrator' in body['note']

    bucket, path, content_type, size = calls['upload']
    assert bucket == 'proof_of_delivery'
    assert path.startswith(f'deliveries/{DELIVERY_ID}/') and path.endswith('.jpg')
    assert content_type == 'image/jpeg' and size > 0

    fn, args = calls['rpc']
    assert fn == 'submit_delivery_proof'
    assert args['p_delivery_id'] == DELIVERY_ID
    assert args['p_transporter_id'] == 't1'
    # Only the digest crosses the wire, and it is the digest of what was typed.
    assert args['p_code_hash'] == hashlib.sha256(b'123456').hexdigest()
    assert args['p_photo_path'] == path


def test_a_wrong_code_is_not_reported_as_success(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    monkeypatch.setattr(app_mod.SUPA, 'upload_private_media',
                        lambda *a: RpcResult(ok=True, data={}))
    monkeypatch.setattr(app_mod.SUPA, 'rpc', lambda fn, args: RpcResult(
        ok=True, data={'ok': True, 'code_matched': False, 'attempts_remaining': 4}))
    r = _proof_post(client, code='999999')
    assert r.status_code == 200
    body = r.get_json()
    assert body['code_matched'] is False
    assert body['attempts_remaining'] == 4
    assert 'cannot be confirmed' in body['error']


def test_a_locked_code_is_a_429_and_the_upload_is_cleaned_up(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    deleted = []
    monkeypatch.setattr(app_mod.SUPA, 'upload_private_media',
                        lambda *a: RpcResult(ok=True, data={}))
    monkeypatch.setattr(app_mod.SUPA, 'rpc', lambda fn, args: RpcResult(
        ok=True, data={'ok': False, 'reason': 'locked_out'}))
    monkeypatch.setattr(app_mod.SUPA, 'delete_object',
                        lambda bucket, path: deleted.append(path) or True)
    r = _proof_post(client)
    assert r.status_code == 429 and r.get_json()['code'] == 'code_locked'
    assert deleted and deleted[0].startswith(f'deliveries/{DELIVERY_ID}/')


def test_proof_rejects_a_file_that_is_not_really_an_image(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    monkeypatch.setattr(app_mod.SUPA, 'upload_private_media',
                        lambda *a: pytest.fail('a spoofed file must not be stored'))
    r = _proof_post(client, content=b'<html>not an image</html>')
    assert r.status_code == 415


def test_proof_requires_a_photograph_and_a_6_digit_code(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    r = client.post(f'/api/transporter/deliveries/{DELIVERY_ID}/proof',
                    data={'code': '123456'}, content_type='multipart/form-data')
    assert r.status_code == 400 and r.get_json()['code'] == 'photo_required'
    r = _proof_post(client, code='12ab')
    assert r.status_code == 400 and r.get_json()['code'] == 'code_format'


def test_a_storage_failure_records_nothing(client, monkeypatch, as_transporter):
    _db(monkeypatch, transporters=[T1])
    monkeypatch.setattr(app_mod.SUPA, 'upload_private_media',
                        lambda *a: RpcResult(ok=False, status=503, error='down'))
    monkeypatch.setattr(app_mod.SUPA, 'rpc',
                        lambda *a: pytest.fail('no proof may be recorded without the photo'))
    r = _proof_post(client)
    assert r.status_code == 503
    assert r.get_json()['code'] == 'proof_storage_unavailable'


# ── Admin: codes, confirmation, fees, rates ─────────────────────────────────
def test_issuing_a_code_returns_it_once_and_stores_only_a_hash(client, monkeypatch):
    monkeypatch.setattr(app_mod, '_log_system_event', lambda *a, **k: None)
    _db(monkeypatch, deliveries=[{
        'id': DELIVERY_ID, 'order_id': ORDER_ID, 'status': 'in_transit',
        'transporter_id': 't1'}])
    writes = []
    monkeypatch.setattr(app_mod.SUPA, 'update_checked', lambda table, data, filters: (
        writes.append(data) or RpcResult(ok=True, data=[{'id': DELIVERY_ID}])))

    r = client.post(f'/api/admin/deliveries/{DELIVERY_ID}/proof-code',
                    headers=_admin_headers())
    assert r.status_code == 200
    body = r.get_json()
    code = body['code']
    assert len(code) == 6 and code.isdigit()
    stored = writes[0]['proof_code_hash']
    assert stored == hashlib.sha256(code.encode()).hexdigest()
    assert code not in str(writes[0])
    assert 'proof_code_hash' not in body


def test_confirmation_requires_a_matching_proof_once_a_code_exists(client, monkeypatch):
    monkeypatch.setattr(app_mod, '_log_system_event', lambda *a, **k: None)
    monkeypatch.setattr(app_mod, '_discord_notify', lambda *a, **k: None)
    monkeypatch.setattr(app_mod.NOTIFY, 'enqueue', lambda **k: None)
    order = {'id': 'o1', 'payment_ref': 'AB-1', 'payment_method': 'momo',
             'payment_status': 'paid', 'status': 'in_transit',
             'total_amount': 50000, 'total_price': 50000,
             'quantity_kg': 1, 'price_per_kg': 50000}
    delivery = {'id': DELIVERY_ID, 'proof_code_issued_at': '2026-10-27T08:00:00Z',
                'proof_submitted_at': None, 'proof_code_matches': None}

    def fake_get(table, filters=None, limit=100):
        if table == 'orders':
            return [order]
        if table == 'deliveries':
            return [delivery]
        return []

    monkeypatch.setattr(app_mod, 'supa_get', fake_get)
    monkeypatch.setattr(app_mod.SUPA, 'rpc',
                        lambda *a: pytest.fail('an unevidenced delivery must not advance'))
    r = client.post('/api/admin/orders/o1/confirm-delivery', json={},
                    headers=_admin_headers())
    assert r.status_code == 409 and r.get_json()['code'] == 'proof_missing'

    delivery['proof_submitted_at'] = '2026-10-27T09:00:00Z'
    delivery['proof_code_matches'] = False
    r = client.post('/api/admin/orders/o1/confirm-delivery', json={},
                    headers=_admin_headers())
    assert r.status_code == 409 and r.get_json()['code'] == 'proof_code_mismatch'

    delivery['proof_code_matches'] = True
    updates = []
    monkeypatch.setattr(app_mod.SUPA, 'rpc', lambda fn, args: RpcResult(ok=True, data=True))
    monkeypatch.setattr(app_mod, 'supa_update', lambda table, data, col, val: (
        updates.append((table, data)) or True))
    r = client.post('/api/admin/orders/o1/confirm-delivery', json={},
                    headers=_admin_headers())
    assert r.status_code == 200
    delivered = [d for t, d in updates if t == 'deliveries'][0]
    assert delivered['proof_verified_by'] == 'admin'
    assert delivered['proof_verified_at']


def test_an_admin_accepting_an_offer_is_what_sets_the_fee(client, monkeypatch):
    monkeypatch.setattr(app_mod, '_log_system_event', lambda *a, **k: None)
    _db(monkeypatch, deliveries=[{
        'id': DELIVERY_ID, 'offer_status': 'proposed',
        'transporter_offer_ugx': 62000, 'transport_fee_ugx': None}])
    writes = []
    monkeypatch.setattr(app_mod.SUPA, 'update_checked', lambda table, data, filters: (
        writes.append((data, filters)) or RpcResult(ok=True, data=[{'id': DELIVERY_ID}])))
    r = client.post(f'/api/admin/deliveries/{DELIVERY_ID}/offer',
                    json={'decision': 'accept'}, headers=_admin_headers())
    assert r.status_code == 200
    fields, filters = writes[0]
    assert fields['transport_fee_ugx'] == 62000
    assert fields['fee_basis'] == 'negotiated'
    assert fields['offer_status'] == 'accepted'
    # The update only lands while the offer is still open.
    assert filters['offer_status'] == 'eq.proposed'


def test_rejecting_an_offer_does_not_touch_the_fee(client, monkeypatch):
    monkeypatch.setattr(app_mod, '_log_system_event', lambda *a, **k: None)
    _db(monkeypatch, deliveries=[{
        'id': DELIVERY_ID, 'offer_status': 'proposed',
        'transporter_offer_ugx': 62000, 'transport_fee_ugx': 45000}])
    writes = []
    monkeypatch.setattr(app_mod.SUPA, 'update_checked', lambda table, data, filters: (
        writes.append(data) or RpcResult(ok=True, data=[{'id': DELIVERY_ID}])))
    r = client.post(f'/api/admin/deliveries/{DELIVERY_ID}/offer',
                    json={'decision': 'reject'}, headers=_admin_headers())
    assert r.status_code == 200
    assert 'transport_fee_ugx' not in writes[0]


def test_setting_a_fee_records_its_basis(client, monkeypatch):
    monkeypatch.setattr(app_mod, '_log_system_event', lambda *a, **k: None)
    _db(monkeypatch, deliveries=[{'id': DELIVERY_ID, 'order_id': ORDER_ID,
                                  'transport_fee_ugx': None}])
    writes = []
    monkeypatch.setattr(app_mod.SUPA, 'update_checked', lambda table, data, filters: (
        writes.append(data) or RpcResult(ok=True, data=[{'id': DELIVERY_ID}])))
    r = client.post(f'/api/admin/deliveries/{DELIVERY_ID}/fee',
                    json={'amount_ugx': 48000, 'basis': 'calculated',
                          'quote_inputs': {'distance_km': 120, 'weight_kg': 800},
                          'disclose_to_buyer': True},
                    headers=_admin_headers())
    assert r.status_code == 200
    fields = writes[0]
    assert fields['transport_fee_ugx'] == 48000
    assert fields['fee_basis'] == 'calculated'
    assert fields['fee_quote_inputs'] == {'distance_km': 120, 'weight_kg': 800}
    assert fields['fee_disclosed_to_buyer'] is True


def test_quote_says_not_configured_instead_of_zero(client, monkeypatch):
    monkeypatch.setattr(app_mod.SUPA, 'rpc', lambda fn, args: RpcResult(
        ok=True, data={'status': 'not_configured',
                       'message': 'No transport rates are configured for lorry.'}))
    r = client.post('/api/admin/transport-quote',
                    json={'vehicle_type': 'lorry', 'distance_km': 40, 'weight_kg': 300},
                    headers=_admin_headers())
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'not_configured'
    assert 'total' not in body


def test_the_rate_table_is_empty_and_writes_are_validated(client, monkeypatch):
    _db(monkeypatch, deliveries=[])
    monkeypatch.setattr(app_mod, 'supa_get', lambda table, filters=None, limit=100: [])
    monkeypatch.setattr(app_mod, '_log_system_event', lambda *a, **k: None)
    body = client.get('/api/admin/transport-rates', headers=_admin_headers()).get_json()
    assert body['count'] == 0 and body['rates'] == []
    assert 'Nothing is assumed' in body['note']

    r = client.put('/api/admin/transport-rates/lorry',
                   json={'base_fee_ugx': -1, 'ugx_per_km': 800, 'ugx_per_kg': 20},
                   headers=_admin_headers())
    assert r.status_code == 400

    upserts = []
    monkeypatch.setattr(app_mod.SUPA, 'upsert', lambda table, data, on_conflict=None: (
        upserts.append((table, data, on_conflict)) or RpcResult(ok=True, data=[data])))
    r = client.put('/api/admin/transport-rates/lorry',
                   json={'base_fee_ugx': 20000, 'ugx_per_km': 800, 'ugx_per_kg': 20,
                         'night_surcharge_pct': 10},
                   headers=_admin_headers())
    assert r.status_code == 200
    table, data, on_conflict = upserts[0]
    assert table == 'transport_rate_config' and on_conflict == 'vehicle_type'
    assert data['vehicle_type'] == 'lorry' and data['base_fee_ugx'] == 20000
    assert data['updated_by'] == 'admin'
