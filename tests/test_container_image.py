"""Opt-in tests of the built image, using fresh volumes and no network access.

STARWAVE_CONTAINER_IMAGE=starwave:local uv run pytest tests/test_container_image.py
"""

import json
import os
import subprocess
import time
import uuid

import pytest

IMAGE = os.environ.get('STARWAVE_CONTAINER_IMAGE')
pytestmark = pytest.mark.skipif(not IMAGE, reason='Set STARWAVE_CONTAINER_IMAGE to test a built image')


def docker(*arguments, timeout=90):
    result = subprocess.run(
        ['docker', *arguments], capture_output=True, text=True, timeout=timeout, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_image_is_non_root_and_contains_codec_without_private_host_files():
    config = json.loads(docker('image', 'inspect', IMAGE))[0]['Config']
    assert config['User'] not in ('', '0', 'root', '0:0', 'root:root')
    assert config['Healthcheck']['Test']
    docker(
        'run', '--rm', '--network', 'none', '--entrypoint', 'python', IMAGE, '-c',
        "import os, pathlib, shutil, sqlite3; "
        "from backend.codec import DMRDecoder; "
        "assert os.getuid() != 0; "
        "assert sqlite3.sqlite_version == '3.53.1'; "
        "assert pathlib.Path('/app/LICENSE').is_file(); "
        "assert pathlib.Path('/app/SQLITE_PROVENANCE.txt').is_file(); "
        "assert pathlib.Path('/app/native/build/THIRD_PARTY_LICENSES.txt').is_file(); "
        "assert pathlib.Path('/app/web/dist/fonts/OFL.txt').is_file(); "
        "assert not any(pathlib.Path(p).exists() for p in "
        "('/app/.env','/app/data','/app/.cache','/app/.git','/app/.ssh')); "
        "assert shutil.which('gcc') is None; assert shutil.which('node') is None; "
        "d=DMRDecoder(); "
        "burst=bytes.fromhex('b9e881526173002a6bb9e881526000000000000173002a6bb9e881526173002a6b'); "
        "assert len(d.decode(burst)) == 960; assert d.last_errors == (0,0,0); d.close()"
    )


def test_offline_standby_and_container_recreation_preserve_database_audio_and_cache():
    suffix = uuid.uuid4().hex[:12]
    name = f'starwave-test-{suffix}'
    data_volume = f'{name}-data'
    model_volume = f'{name}-models'
    docker('volume', 'create', data_volume)
    docker('volume', 'create', model_volume)

    def start():
        docker(
            'run', '--detach', '--name', name, '--network', 'none', '--init',
            '--health-interval', '1s', '--health-start-period', '2s',
            '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true',
            '--tmpfs', '/tmp:rw,nosuid,noexec,size=64m',
            '--mount', f'type=volume,source={data_volume},target=/data',
            '--mount', f'type=volume,source={model_volume},target=/models',
            '-e', 'PI_STAR_HOST=pi-star@example.invalid',
            '-e', 'REQUIRE_EXPLICIT_SSH=true',
            '-e', 'OPENAI_API_KEY=', '-e', 'CONTROL_TOKEN=',
            IMAGE,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = subprocess.run(
                ['docker', 'exec', name, 'python', '-c',
                 "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/api/health',timeout=1).close()"],
                capture_output=True, text=True, timeout=5,
                check=False,
            )
            if result.returncode == 0 and docker(
                'inspect', '--format', '{{.State.Health.Status}}', name
            ) == 'healthy':
                return
            if docker('inspect', '--format', '{{.State.Running}}', name) != 'true':
                pytest.fail('Isolated container exited before becoming healthy: ' + docker('logs', name))
            time.sleep(.2)
        pytest.fail('Isolated container did not become healthy: ' + docker('logs', name))

    try:
        start()
        # A configured hostname alone must not start SSH or model loading.
        docker(
            'exec', name, 'python', '-c',
            "import json,urllib.request,urllib.error; "
            "request=urllib.request.Request('http://127.0.0.1:8788/api/listening/start', "
            "data=b'{}',headers={'Content-Type':'application/json', "
            "'X-Starwave-Request':'1','Origin':'http://127.0.0.1:8788'},method='POST'); "
            "response=None\n"
            "try:\n"
            "    response=urllib.request.urlopen(request,timeout=3)\n"
            "except urllib.error.HTTPError as error:\n"
            "    assert error.code == 400\n"
            "else:\n"
            "    raise AssertionError('Listening started without explicit SSH credentials')\n"
            "status=json.load(urllib.request.urlopen('http://127.0.0.1:8788/api/status')); "
            "assert status['listening_state']=='stopped'"
        )
        docker(
            'exec', name, 'python', '-c',
            "import json,urllib.request,pathlib,stat; "
            "body={'base_url':'https://provider.example.test/v1','model':'smoke-model',"
            "'api_mode':'chat_completions','enabled':True,'api_key':'synthetic-container-key'}; "
            "request=urllib.request.Request('http://127.0.0.1:8788/api/translation-settings',"
            "data=json.dumps(body).encode(),headers={'Content-Type':'application/json',"
            "'X-Starwave-Request':'1','Origin':'http://127.0.0.1:8788'},method='PUT'); "
            "result=json.load(urllib.request.urlopen(request,timeout=3)); "
            "assert result['ok'] and result['api_key_configured']; assert 'api_key' not in result; "
            "document=json.loads(pathlib.Path('/data/translation.json').read_text()); "
            "keyfile=pathlib.Path('/data/.translation')/document['generation']/'api_key'; "
            "assert stat.S_IMODE(keyfile.stat().st_mode)==0o600; "
            "assert 'synthetic-container-key' not in json.dumps(document)"
        )
        docker(
            'exec', name, 'python', '-c',
            "import json,urllib.request,pathlib,sqlite3,wave; "
            "health=json.load(urllib.request.urlopen('http://127.0.0.1:8788/api/health')); "
            "assert health['ok']; "
            "status=json.load(urllib.request.urlopen('http://127.0.0.1:8788/api/status')); "
            "assert status['listening_state']=='stopped'; "
            "root=pathlib.Path('/data'); assert (root/'calls.sqlite3').is_file(); "
            "db=sqlite3.connect(root/'calls.sqlite3'); "
            "assert db.execute('pragma journal_mode').fetchone()[0]=='wal'; "
            "db.execute('create table container_smoke (value text)'); "
            "db.execute(\"insert into container_smoke values ('persisted')\"); db.commit(); db.close(); "
            "(root/'audio').mkdir(exist_ok=True); "
            "w=wave.open(str(root/'audio'/'smoke.wav'),'wb'); w.setparams((1,2,8000,480,'NONE','not compressed')); "
            "w.writeframes(bytes(960)); w.close(); "
            "(root/'radioids.json').write_text('{}'); "
            "pathlib.Path('/models/cache/smoke.marker').write_text('persisted')"
        )
        docker('stop', '--time', '20', name)
        # Uvicorn re-raises SIGTERM after its graceful shutdown completes.
        assert docker('inspect', '--format', '{{.State.ExitCode}}', name) in ('0', '143')
        docker('rm', name)
        start()
        docker(
            'exec', name, 'python', '-c',
            "import pathlib,sqlite3,wave,json,urllib.request; "
            "status=json.load(urllib.request.urlopen('http://127.0.0.1:8788/api/status')); "
            "assert status['listening_state']=='stopped'; "
            "assert status['ai']['model']=='smoke-model'; "
            "provider=json.load(urllib.request.urlopen('http://127.0.0.1:8788/api/translation-settings')); "
            "assert provider['base_url']=='https://provider.example.test/v1' and provider['api_key_configured']; "
            "assert provider['api_mode']=='chat_completions' and 'api_key' not in provider; "
            "db=sqlite3.connect('/data/calls.sqlite3'); "
            "assert db.execute('select value from container_smoke').fetchone()[0]=='persisted'; db.close(); "
            "assert pathlib.Path('/data/radioids.json').read_text()=='{}'; "
            "assert pathlib.Path('/models/cache/smoke.marker').read_text()=='persisted'; "
            "w=wave.open('/data/audio/smoke.wav'); assert w.getnframes()==480; w.close()"
        )
    finally:
        subprocess.run(['docker', 'rm', '--force', name], capture_output=True, timeout=30, check=False)
        subprocess.run(['docker', 'volume', 'rm', data_volume, model_volume], capture_output=True, timeout=30, check=False)
