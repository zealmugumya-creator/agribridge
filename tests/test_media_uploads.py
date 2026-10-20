# Copyright (C) 2026 Mugumya Zeal. All rights reserved.

"""Media upload boundary tests.

The browser may never write directly to Storage: only a verified user can send
a validated image/video to the backend, which uses the service-role client.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from conftest import RpcResult

import app as app_mod


@pytest.fixture
def client():
    app_mod.app.config['TESTING'] = True
    with app_mod.app.test_client() as c:
        yield c


def _jpeg() -> bytes:
    # JPEG start/end markers are sufficient for the API's lightweight magic
    # validation; image decoding/scanning belongs in an asynchronous media job.
    return b'\xff\xd8\xff\xe0' + b'JFIF\x00' + (b'0' * 32) + b'\xff\xd9'


def _prepare_authenticated_upload(monkeypatch):
    monkeypatch.setattr(app_mod, 'verify_supabase_user', lambda: ('farmer-1', None))
    monkeypatch.setattr(app_mod, 'legal_gate', lambda _uid: None)
    monkeypatch.setattr(app_mod, '_log_system_event', lambda *a, **kw: None)


def test_upload_requires_verified_session(client):
    r = client.post('/api/uploads', data={'kind': 'listings'})
    assert r.status_code == 401


def test_upload_rejects_disguised_file(client, monkeypatch):
    _prepare_authenticated_upload(monkeypatch)
    r = client.post('/api/uploads', data={
        'kind': 'listings',
        'file': (BytesIO(b'<html>not an image</html>'), 'photo.jpg', 'image/jpeg'),
    }, content_type='multipart/form-data')
    assert r.status_code == 415
    assert 'does not match' in r.get_json()['error']


def test_upload_stores_under_verified_user_path(client, monkeypatch):
    _prepare_authenticated_upload(monkeypatch)
    captured = {}

    class FakeStorage:
        def upload_public_media(self, path, content, content_type):
            captured.update(path=path, content=content, content_type=content_type)
            return RpcResult(ok=True, data={'url': 'https://media.example/listing.jpg'}, status=201)

    monkeypatch.setattr(app_mod, 'SUPA', FakeStorage())
    r = client.post('/api/uploads', data={
        'kind': 'listings',
        'file': (BytesIO(_jpeg()), 'anything.jpg', 'image/jpeg'),
    }, content_type='multipart/form-data')

    assert r.status_code == 201
    assert r.get_json() == {'ok': True, 'url': 'https://media.example/listing.jpg'}
    assert captured['path'].startswith('listings/farmer-1/')
    assert captured['path'].endswith('.jpg')
    assert captured['content_type'] == 'image/jpeg'


def test_upload_rejects_unknown_category(client, monkeypatch):
    _prepare_authenticated_upload(monkeypatch)
    r = client.post('/api/uploads', data={
        'kind': 'other-users-folder',
        'file': (BytesIO(_jpeg()), 'photo.jpg', 'image/jpeg'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400
