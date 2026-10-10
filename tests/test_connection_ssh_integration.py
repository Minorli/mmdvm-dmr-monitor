"""Opt-in real OpenSSH password test using an isolated synthetic SSH server.

STARWAVE_CONNECTION_IMAGE=starwave:tag uv run pytest tests/test_connection_ssh_integration.py
No host directories, production keys, real API credentials or published ports.
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get('STARWAVE_CONNECTION_IMAGE')
SERVER_IMAGE = 'starwave-ssh-fixture:integration'
TEST_PASSWORD = 'only-for-isolated-ssh-test'
CONTROL_TOKEN = 'synthetic-integration-control'
pytestmark = pytest.mark.skipif(not IMAGE, reason='Set STARWAVE_CONNECTION_IMAGE for isolated SSH integration')


def docker(*arguments, timeout=120):
    result = subprocess.run(
        ['docker', *arguments], capture_output=True, text=True, timeout=timeout, check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def request(container, method, path, body=None):
    program = (
        "import json,urllib.request,urllib.error; "
        f"body={body!r}; "
        "headers={'X-Starwave-Request':'1','Content-Type':'application/json',"
        "'Origin':'http://127.0.0.1:8788',"
        f"'Authorization':'Bearer {CONTROL_TOKEN}'}}; "
        f"request=urllib.request.Request('http://127.0.0.1:8788{path}',"
        f"data=None if body is None else json.dumps(body).encode(),headers=headers,method={method!r})\n"
        "try:\n"
        "    response=urllib.request.urlopen(request,timeout=25)\n"
        "except urllib.error.HTTPError as error:\n"
        "    response=error\n"
        "print(json.dumps({'status':response.code,'body':json.loads(response.read())}))"
    )
    return json.loads(docker('exec', container, 'python', '-c', program, timeout=30))


def test_real_password_login_host_trust_and_private_configuration_survive_recreation():
    docker('build', '--tag', SERVER_IMAGE, str(ROOT / 'tests/fixtures/ssh-password-server'), timeout=1200)
    suffix = uuid.uuid4().hex[:12]
    prefix = f'starwave-ssh-test-{suffix}'
    network, server, app = prefix + '-net', prefix + '-server', prefix + '-app'
    data, models = prefix + '-data', prefix + '-models'
    docker('network', 'create', '--internal', network)
    docker('volume', 'create', data)
    docker('volume', 'create', models)

    def start_app():
        docker(
            'run', '--detach', '--name', app, '--network', network, '--init',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--tmpfs', '/tmp:rw,nosuid,noexec,size=64m',
            '--mount', f'type=volume,source={data},target=/data',
            '--mount', f'type=volume,source={models},target=/models',
            '-e', 'PI_STAR_HOST=', '-e', 'OPENAI_API_KEY=',
            '-e', 'TRANSLATION_ENABLED=false', '-e', f'CONTROL_TOKEN={CONTROL_TOKEN}',
            IMAGE,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = subprocess.run(
                ['docker', 'exec', app, 'python', '-c',
                 "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/api/health',timeout=1).close()"],
                capture_output=True, timeout=5, check=False,
            )
            if result.returncode == 0:
                return
            time.sleep(.2)
        pytest.fail('Isolated application did not become healthy')

    try:
        docker('run', '--detach', '--name', server, '--network', network,
               '--network-alias', 'ssh-test', SERVER_IMAGE)
        deadline = time.monotonic() + 20
        fingerprint = None
        while time.monotonic() < deadline:
            result = subprocess.run(
                ['docker', 'exec', server, 'ssh-keygen', '-lf',
                 '/etc/ssh/ssh_host_ed25519_key.pub', '-E', 'sha256'],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode == 0:
                fingerprint = result.stdout.split()[1]
                break
            time.sleep(.2)
        assert fingerprint is not None
        start_app()
        details = {'host':'ssh-test','port':22,'username':'listener',
                   'auth_mode':'password','password':TEST_PASSWORD}
        first = request(app, 'POST', '/api/connection/test', details)
        assert first['status'] == 200
        assert first['body']['ok'] is False
        assert first['body']['needs_trust'] is True
        assert first['body']['host_key']['fingerprint'] == fingerprint
        trusted = {**details, 'trust_fingerprint':fingerprint}
        probe = request(app, 'POST', '/api/connection/test', trusted)
        assert probe['status'] == 200 and probe['body']['ok'] is True
        saved = request(app, 'PUT', '/api/connection', trusted)
        assert 200 <= saved['status'] < 300
        public = request(app, 'GET', '/api/connection')['body']
        assert public['host'] == 'ssh-test' and public['username'] == 'listener'
        assert public['auth_mode'] == 'password'
        assert TEST_PASSWORD not in json.dumps(public)
        assert 'password' not in public and 'password_file' not in public
        assert request(app, 'GET', '/api/status')['body']['listening_state'] == 'stopped'
        docker('stop', '--time', '20', app)
        docker('rm', app)
        start_app()
        recreated = request(app, 'GET', '/api/connection')['body']
        assert recreated == public
        incorrect = request(app, 'POST', '/api/connection/test', {**trusted,'password':'wrong-synthetic-password'})
        assert incorrect['status'] == 200 and incorrect['body']['ok'] is False
        changed = {**trusted,'username':'other-listener','trust_fingerprint':'SHA256:' + 'A' * 43}
        rejected = request(app, 'PUT', '/api/connection', changed)
        assert rejected['status'] == 200 and rejected['body']['ok'] is False
        assert request(app, 'GET', '/api/connection')['body'] == public
        assert request(app, 'GET', '/api/status')['body']['listening_state'] == 'stopped'
    finally:
        subprocess.run(['docker', 'rm', '--force', app, server], capture_output=True, timeout=30, check=False)
        subprocess.run(['docker', 'volume', 'rm', data, models], capture_output=True, timeout=30, check=False)
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True, timeout=30, check=False)
