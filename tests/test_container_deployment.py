"""Portable service rendering without touching the installed service."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def isolated_environment(tmp_path):
    # A different XDG_CONFIG_HOME does not isolate the running systemd manager.
    # Stub systemctl for every deployment test, even expected read-only modes.
    executable_dir = tmp_path / 'fake-bin'
    executable_dir.mkdir()
    stub = executable_dir / 'systemctl'
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SYSTEMCTL_LOG"\n')
    stub.chmod(0o755)
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(mode=0o700)
    return {
        'PATH': f'{executable_dir}:{os.environ["PATH"]}',
        'HOME': str(tmp_path),
        'XDG_CONFIG_HOME': str(tmp_path / 'user-config'),
        'XDG_RUNTIME_DIR': str(runtime_dir),
        'DBUS_SESSION_BUS_ADDRESS': f'unix:path={runtime_dir}/bus',
        'SYSTEMCTL_LOG': str(tmp_path / 'systemctl.log'),
    }


def test_service_can_be_rendered_from_a_checkout_with_special_characters(tmp_path):
    checkout = tmp_path / 'checkout with spaces % and $'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'deploy').mkdir()
    shutil.copy2(ROOT / 'scripts/install-service.sh', checkout / 'scripts')
    shutil.copy2(ROOT / 'deploy/pi-star-record.service', checkout / 'deploy')
    start = checkout / 'scripts/start.sh'
    start.write_text('#!/bin/sh\nexit 0\n')
    start.chmod(0o755)
    config_dir = tmp_path / 'user-config'
    environment = isolated_environment(tmp_path)
    result = subprocess.run(
        [str(checkout / 'scripts/install-service.sh'), '--print'],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert 'WorkingDirectory=' + str(checkout).replace('%', '%%') in result.stdout
    assert str(checkout).replace('%', '%%') in result.stdout
    assert '$/scripts/start.sh' in result.stdout
    assert '@PROJECT_ROOT@' not in result.stdout
    assert str(ROOT) not in result.stdout
    assert not config_dir.exists()
    assert not (tmp_path / 'systemctl.log').exists()
    if shutil.which('systemd-analyze'):
        unit_file = tmp_path / 'pi-star-record.service'
        unit_file.write_text(result.stdout)
        verified = subprocess.run(
            ['systemd-analyze', '--user', 'verify', str(unit_file)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert verified.returncode == 0, verified.stderr


def test_service_install_rejects_unknown_option_without_side_effects(tmp_path):
    config_dir = tmp_path / 'user-config'
    result = subprocess.run(
        [str(ROOT / 'scripts/install-service.sh'), '--unknown'],
        env=isolated_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert not config_dir.exists()
    assert not (tmp_path / 'systemctl.log').exists()


def test_missing_local_build_does_not_install_or_control_service(tmp_path):
    checkout = tmp_path / 'unbuilt-checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'deploy').mkdir()
    shutil.copy2(ROOT / 'scripts/install-service.sh', checkout / 'scripts')
    shutil.copy2(ROOT / 'deploy/pi-star-record.service', checkout / 'deploy')
    result = subprocess.run(
        [str(checkout / 'scripts/install-service.sh')],
        env=isolated_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert 'complete the documented local build' in result.stderr
    assert not (tmp_path / 'user-config').exists()
    assert not (tmp_path / 'systemctl.log').exists()


def test_container_bind_sources_must_be_absolute_before_creating_data(tmp_path):
    environment = isolated_environment(tmp_path)
    fake_id = tmp_path / 'fake-bin/id'
    fake_id.write_text('#!/bin/sh\nprintf "1000\\n"\n')
    fake_id.chmod(0o755)
    data_dir = tmp_path / 'data'
    result = subprocess.run(
        [str(ROOT / 'scripts/container-entrypoint.sh'), 'true'],
        env={
            **environment,
            'DATA_DIR': str(data_dir),
            'CACHE_DIR': str(tmp_path / 'cache'),
            'HOST_DATA_DIR': 'relative-data',
            'HOST_MODEL_CACHE_DIR': str(tmp_path / 'model-source'),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert 'must be absolute paths' in result.stderr
    assert not data_dir.exists()
