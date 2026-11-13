"""Integration test for the isolated Linux SSH/PAM fixture in this directory."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient


PRIVATE = '/srv/rf-private'


def run_test():
    assert Path('/etc/rf-elevation-test-image').is_file(), 'Run this test in its fixture container.'
    assert os.getuid() != 0, 'The API must run as the ordinary test user.'
    assert subprocess.run(['sudo', '-n', 'true'], capture_output=True).returncode != 0
    login_password = os.environ.pop('RF_TEST_LOGIN_PASSWORD')
    root_password = os.environ.pop('RF_TEST_ROOT_PASSWORD')

    with TemporaryDirectory() as home, patch.dict(os.environ, {
        'RF_HOME': home, 'PYTHON_KEYRING_BACKEND': 'keyring.backends.null.Keyring', 'RF_SYNC_DISABLE': '1',
    }):
        from app.main import app

        with TestClient(app, headers={'X-RF-Request': '1'}) as client:
            created = client.post('/api/sessions', json={
                'host': '127.0.0.1', 'port': 2222, 'username': 'rfuser', 'rememberPassword': False,
            }).raise_for_status()
            sid = created.json()['id']
            session = f'/api/sessions/{sid}'
            client.post(session + '/connect', json={'password': login_password, 'remember': False}).raise_for_status()

            def listing(path):
                return client.get(session + '/list', params={'path': path})

            def assert_normal_access():
                state, = client.get('/api/sessions').raise_for_status().json()
                assert state['id'] == sid and state['connected'] and not state['sudo']
                assert state['elevationMethod'] is None
                assert listing('/home/rfuser').status_code == 200
                denied = listing(PRIVATE)
                assert denied.status_code == 403 and denied.json().get('permissionDenied'), denied.text

            try:
                assert_normal_access()
                rejected = client.post(session + '/sudo', json={
                    'method': 'su', 'enable': True, 'password': secrets.token_hex(24),
                })
                assert 400 <= rejected.status_code < 500, rejected.status_code
                assert_normal_access()

                elevated = client.post(session + '/sudo', json={
                    'method': 'su', 'enable': True, 'password': root_password,
                }).raise_for_status().json()
                assert elevated['sudo'] and elevated['elevationMethod'] == 'su'
                contents = listing(PRIVATE).raise_for_status().json()['entries']
                assert any(item['name'] == 'seed.txt' for item in contents)
                marker = client.post(session + '/download', json={'paths': [PRIVATE + '/seed.txt']})
                assert marker.raise_for_status().content == b'fixture marker\n'

                payload = secrets.token_bytes(1024)
                transfer = client.post(session + '/upload',
                    data={'dest': PRIVATE, 'relatives': json.dumps(['nested/file.bin'])},
                    files={'files': ('file.bin', payload, 'application/octet-stream')},
                ).raise_for_status()
                last = json.loads(transfer.text.strip().splitlines()[-1])
                assert last.get('ok'), last
                download = client.post(session + '/download', json={'paths': [PRIVATE + '/nested/file.bin']})
                assert download.raise_for_status().content == payload

                disabled = client.post(session + '/sudo', json={'enable': False}).raise_for_status().json()
                assert not disabled['sudo'] and disabled['elevationMethod'] is None
                assert_normal_access()
                denied = client.post(session + '/download', json={'paths': [PRIVATE + '/nested/file.bin']})
                assert denied.status_code == 403, denied.status_code
                for path in Path(home).rglob('*'):
                    if path.is_file():
                        assert root_password.encode() not in path.read_bytes(), 'Root password persisted to RF_HOME'
            finally:
                client.post(session + '/disconnect').raise_for_status()


if __name__ == '__main__':
    run_test()
    print('PASS: PAM authorizes the supplied root password, transfers work, and disabling su restores normal access.')
