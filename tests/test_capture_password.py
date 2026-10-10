"""Exercise password transport with synthetic credentials and local helpers only."""

import asyncio
import errno
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.capture import CaptureError, SSHCapture

SYNTHETIC_PASSWORD = '  synthetic-only paßword  '.encode()
PROBE_SCRIPT = b"print('synthetic read-only probe')\n"
REAL_SSHPASS = shutil.which('sshpass')
REAL_SSH = shutil.which('ssh')


@pytest.fixture
def connection(tmp_path, monkeypatch):
    password = tmp_path / 'password'
    password.write_bytes(SYNTHETIC_PASSWORD + b'\n')
    password.chmod(0o600)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    helper = binaries / 'sshpass'
    helper.write_text(
        '#!' + sys.executable + '\n'
        'import hashlib,json,os,sys,time\n'
        'fd=int(sys.argv[sys.argv.index("-d")+1])\n'
        'secret=os.read(fd,4096)\n'
        'os.close(fd)\n'
        'script=sys.stdin.buffer.read()\n'
        'print(json.dumps({"type":"ready", "password_hash":hashlib.sha256(secret).hexdigest(), '
        '"script_hash":hashlib.sha256(script).hexdigest(), '
        '"app_secrets_in_env":any(k in os.environ for k in ("SSHPASS","OPENAI_API_KEY","CONTROL_TOKEN"))}),flush=True)\n'
        'time.sleep(60)\n'
    )
    helper.chmod(0o755)
    # A regression must never fall back to the real SSH client during tests.
    fake_ssh = binaries / 'ssh'
    fake_ssh.write_text('#!/bin/sh\nexit 99\n')
    fake_ssh.chmod(0o755)
    monkeypatch.setenv('PATH', str(binaries) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('SSHPASS', 'synthetic-env-only')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-api-only')
    monkeypatch.setenv('CONTROL_TOKEN', 'synthetic-control-only')
    return SimpleNamespace(
        pi_host='operator@station.example.invalid', ssh_port=2222,
        ssh_key_file=tmp_path / 'unused-key', ssh_password_file=password,
        ssh_known_hosts_file=tmp_path / 'known_hosts', capture_timeout=2,
    )


def assert_closed(descriptors):
    for descriptor in descriptors:
        with pytest.raises(OSError) as error:
            os.fstat(descriptor)
        assert error.value.errno == errno.EBADF


def test_password_fd_and_probe_stdin_are_independent(connection, monkeypatch):
    original_spawn = asyncio.create_subprocess_exec
    observed = {}

    async def spawn(*arguments, **options):
        observed.update(arguments=arguments, options=options)
        return await original_spawn(*arguments, **options)

    monkeypatch.setattr('backend.capture.asyncio.create_subprocess_exec', spawn)

    async def run():
        capture = SSHCapture(connection)
        stream = capture.events(script=PROBE_SCRIPT)
        try:
            event = await anext(stream)
            assert event['password_hash'] == hashlib.sha256(SYNTHETIC_PASSWORD + b'\n').hexdigest()
            assert event['script_hash'] == hashlib.sha256(PROBE_SCRIPT).hexdigest()
            assert event['app_secrets_in_env'] is False
            arguments = observed['arguments']
            assert arguments[:2] == ('sshpass', '-d')
            assert SYNTHETIC_PASSWORD.decode() not in arguments
            assert '-p' in arguments and '2222' in arguments
            assert 'BatchMode=no' in arguments
            assert 'NumberOfPasswordPrompts=1' in arguments
            assert 'PubkeyAuthentication=no' in arguments
            assert 'StrictHostKeyChecking=yes' in arguments
            assert 'ForwardAgent=no' in arguments
            assert 'ClearAllForwardings=yes' in arguments
            assert '-i' not in arguments
            assert_closed(observed['options']['pass_fds'])
            process = capture.process
        finally:
            await stream.aclose()
        assert process.returncode is not None
        assert capture.process is None

    asyncio.run(run())


def test_spawn_failure_closes_both_password_pipe_ends(connection, monkeypatch):
    original_pipe = os.pipe
    descriptors = []

    def pipe():
        ends = original_pipe()
        descriptors.extend(ends)
        return ends

    async def failing_spawn(*arguments, **options):
        raise OSError('synthetic spawn failure')

    monkeypatch.setattr('backend.capture.os.pipe', pipe)
    monkeypatch.setattr('backend.capture.asyncio.create_subprocess_exec', failing_spawn)

    async def run():
        stream = SSHCapture(connection).events(script=PROBE_SCRIPT)
        with pytest.raises(CaptureError, match='无法启动'):
            await anext(stream)
        assert_closed(descriptors)

    asyncio.run(run())


def test_spawn_cancellation_closes_pipe_and_reaps_spawned_helper(connection, monkeypatch):
    original_spawn = asyncio.create_subprocess_exec
    created = None
    release = None
    processes = []
    descriptors = []

    async def delayed_spawn(*arguments, **options):
        process = await original_spawn(*arguments, **options)
        processes.append(process)
        descriptors.extend(options['pass_fds'])
        created.set()
        await release.wait()
        return process

    monkeypatch.setattr('backend.capture.asyncio.create_subprocess_exec', delayed_spawn)

    async def run():
        nonlocal created, release
        created, release = asyncio.Event(), asyncio.Event()
        capture = SSHCapture(connection)
        stream = capture.events(script=PROBE_SCRIPT)
        task = asyncio.create_task(anext(stream))
        try:
            await asyncio.wait_for(created.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert_closed(descriptors)
            assert processes[0].returncode is not None
            assert capture.process is None
        finally:
            release.set()
            if not task.done():
                task.cancel()
            for process in processes:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            await stream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize('value', [b'', b'a\x00b', b'a\nb', b'a\rb', b'x' * 4096], ids=['empty', 'nul', 'newline', 'carriage-return', 'oversized'])
def test_invalid_password_is_rejected_without_spawning(connection, monkeypatch, value):
    connection.ssh_password_file.write_bytes(value)

    async def forbidden_spawn(*arguments, **options):
        pytest.fail('Invalid password reached the subprocess boundary')

    monkeypatch.setattr('backend.capture.asyncio.create_subprocess_exec', forbidden_spawn)

    async def run():
        with pytest.raises(CaptureError, match='口令'):
            await anext(SSHCapture(connection).events(script=PROBE_SCRIPT))

    asyncio.run(run())


def test_key_authentication_retains_identity_and_strict_host_check(connection):
    connection.ssh_password_file = None
    command = SSHCapture(connection).command()
    assert command[0] == 'ssh'
    assert 'BatchMode=yes' in command
    assert '-p' in command and '2222' in command
    assert ['-i', str(connection.ssh_key_file)] == command[command.index('-i'):command.index('-i') + 2]
    assert 'IdentitiesOnly=yes' in command
    assert 'StrictHostKeyChecking=yes' in command
    assert 'ForwardAgent=no' in command
    assert 'ClearAllForwardings=yes' in command
    assert any(option.startswith('UserKnownHostsFile=') for option in command)


@pytest.mark.skipif(not REAL_SSH, reason='OpenSSH is needed for its local config parser')
@pytest.mark.parametrize('directory', ['data with spaces', 'data with "unmatched', r'data\with spaces'])
def test_known_hosts_path_is_one_openssh_config_value(connection, tmp_path, directory):
    connection.ssh_password_file = None
    connection.ssh_known_hosts_file = tmp_path / directory / 'known_hosts'
    command = SSHCapture(connection).command()
    option = next(value for value in command if value.startswith('UserKnownHostsFile='))
    assert shlex.split(option.partition('=')[2]) == [str(connection.ssh_known_hosts_file)]
    # -G only parses config and exits; -F avoids reading real user/system config.
    parsed = subprocess.run(
        [REAL_SSH, '-G', '-F', '/dev/null', '-o', option, 'example.invalid'],
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
    known_hosts = next(line for line in parsed.stdout.splitlines() if line.startswith('userknownhostsfile '))
    assert known_hosts == 'userknownhostsfile ' + str(connection.ssh_known_hosts_file)


def process_is_running(pid):
    status = Path(f'/proc/{pid}/stat')
    try:
        return status.read_text().split()[2] != 'Z'
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_stop_kills_term_ignoring_descendant_in_its_process_group(connection):
    helper = Path(os.environ['PATH'].split(os.pathsep)[0]) / 'sshpass'
    helper.write_text(
        '#!' + sys.executable + '\n'
        'import json,os,subprocess,sys,time\n'
        'fd=int(sys.argv[sys.argv.index("-d")+1]); os.read(fd,4096); os.close(fd)\n'
        'sys.stdin.buffer.read()\n'
        'child=subprocess.Popen([sys.executable,"-c",'
        '"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"])\n'
        'time.sleep(.1)\n'
        'print(json.dumps({"type":"ready","child_pid":child.pid}),flush=True)\n'
        'time.sleep(60)\n'
    )

    async def run():
        capture = SSHCapture(connection)
        stream = capture.events(script=PROBE_SCRIPT)
        event = await anext(stream)
        process = capture.process
        await asyncio.wait_for(stream.aclose(), 5)
        assert process.returncode is not None
        assert not process_is_running(event['child_pid'])

    asyncio.run(run())


def test_cancelling_a_running_stream_reaps_helper(connection):
    async def run():
        capture = SSHCapture(connection)
        stream = capture.events(script=PROBE_SCRIPT)
        await anext(stream)
        process = capture.process
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert process.returncode is not None
        assert capture.process is None

    asyncio.run(run())


def test_stop_reaps_pipe_holding_child_after_helper_has_exited(connection):
    helper = Path(os.environ['PATH'].split(os.pathsep)[0]) / 'sshpass'
    helper.write_text(
        '#!' + sys.executable + '\n'
        'import json,os,subprocess,sys,time\n'
        'fd=int(sys.argv[sys.argv.index("-d")+1]); os.read(fd,4096); os.close(fd)\n'
        'sys.stdin.buffer.read()\n'
        'child=subprocess.Popen([sys.executable,"-c",'
        '"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"])\n'
        'time.sleep(.1)\n'
        'print(json.dumps({"type":"ready","child_pid":child.pid}),flush=True)\n'
    )

    async def run():
        capture = SSHCapture(connection)
        stream = capture.events(script=PROBE_SCRIPT)
        event = await anext(stream)
        process = capture.process
        await asyncio.sleep(.1)
        assert process.returncode == 0
        try:
            await asyncio.wait_for(stream.aclose(), 5)
            assert not process_is_running(event['child_pid'])
        finally:
            if process_is_running(event['child_pid']):
                os.kill(event['child_pid'], 9)

    asyncio.run(run())


@pytest.mark.skipif(not REAL_SSHPASS, reason='sshpass is needed for a local TTY integration check')
def test_real_sshpass_tty_child_and_probe_input_are_cleanly_stopped(connection, monkeypatch):
    binaries = Path(os.environ['PATH'].split(os.pathsep)[0])
    (binaries / 'sshpass').unlink()
    (binaries / 'sshpass').symlink_to(REAL_SSHPASS)
    fake_ssh = binaries / 'ssh'
    fake_ssh.write_text(
        '#!' + sys.executable + '\n'
        'import hashlib,json,os,sys,termios,time\n'
        'tty=os.open("/dev/tty",os.O_RDWR)\n'
        'mode=termios.tcgetattr(tty); mode[3]&=~termios.ECHO; termios.tcsetattr(tty,termios.TCSANOW,mode)\n'
        'os.write(tty,b"Password: ")\n'
        'password=bytearray()\n'
        'while not password.endswith(b"\\n"): password.extend(os.read(tty,1))\n'
        'os.close(tty)\n'
        'script=sys.stdin.buffer.read()\n'
        'print(json.dumps({"type":"ready","ssh_pid":os.getpid(),"ssh_session":os.getsid(0),'
        '"password_hash":hashlib.sha256(password).hexdigest(),'
        '"script_hash":hashlib.sha256(script).hexdigest()}),flush=True)\n'
        'time.sleep(60)\n'
    )

    async def run():
        capture = SSHCapture(connection)
        stream = capture.events(script=PROBE_SCRIPT)
        try:
            event = await anext(stream)
            assert event['password_hash'] == hashlib.sha256(SYNTHETIC_PASSWORD + b'\n').hexdigest()
            assert event['script_hash'] == hashlib.sha256(PROBE_SCRIPT).hexdigest()
            assert event['ssh_session'] != capture.process.pid
            process = capture.process
        finally:
            await stream.aclose()
        assert process.returncode is not None
        assert not process_is_running(event['ssh_pid'])

    asyncio.run(run())


def test_default_stdin_still_contains_collector_script(connection):
    async def run():
        capture = SSHCapture(connection)
        stream = capture.events()
        try:
            event = await anext(stream)
            collector = Path(__file__).resolve().parents[1] / 'backend/collector.py'
            assert event['script_hash'] == hashlib.sha256(collector.read_bytes()).hexdigest()
        finally:
            await stream.aclose()

    asyncio.run(run())


def test_quickly_exiting_helper_maps_stdin_failure_to_capture_error(connection, monkeypatch):
    processes = []

    async def exited_process(self, password_fd):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', 'pass', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, start_new_session=True,
        )
        await process.wait()
        processes.append(process)
        return process

    monkeypatch.setattr(SSHCapture, '_spawn', exited_process)

    async def run():
        capture = SSHCapture(connection)
        with pytest.raises(CaptureError, match='脚本无法送达'):
            await anext(capture.events(script=PROBE_SCRIPT))
        assert processes[0].returncode == 0
        assert capture.process is None

    asyncio.run(run())
