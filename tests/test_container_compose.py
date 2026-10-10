"""Validate volume replacement without loading real deployment credentials."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_bind_overlay_replaces_both_named_volumes_and_reuses_model_root(tmp_path):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose is required for deployment config validation')
    available = subprocess.run(
        ['docker', 'compose', 'version'],
        env={'PATH': os.environ['PATH'], 'HOME': str(tmp_path)},
        capture_output=True,
        check=False,
    )
    if available.returncode:
        pytest.skip('Docker Compose is required for deployment config validation')
    data = tmp_path / 'data'
    models = tmp_path / 'models'
    result = subprocess.run(
        ['docker', 'compose', '--env-file', '/dev/null', '-f', 'compose.yaml',
         '-f', 'deploy/compose.bind.yaml', 'config', '--format', 'json'],
        cwd=ROOT,
        env={
            'PATH': os.environ['PATH'],
            'HOME': str(tmp_path),
            'HOST_DATA_DIR': str(data),
            'HOST_MODEL_CACHE_DIR': str(models),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    service = json.loads(result.stdout)['services']['starwave']
    assert service['environment']['CACHE_DIR'] == '/models'
    assert service['environment']['REQUIRE_EXPLICIT_SSH'] == 'true'
    assert service['environment']['WHISPER_THREADS'] == '2'
    assert service['cpus'] == 4
    mounts = {volume['target']: volume for volume in service['volumes']}
    assert set(mounts) == {'/data', '/models'}
    assert mounts['/data']['type'] == mounts['/models']['type'] == 'bind'
    assert mounts['/data']['source'] == str(data)
    assert mounts['/models']['source'] == str(models)
    assert not data.exists() and not models.exists()
