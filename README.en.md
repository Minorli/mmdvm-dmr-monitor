# MMDVM DMR Monitor / Starwave

[中文说明](README.md) · [Deployment](docs/deployment.md) · [Architecture](docs/architecture.md)

Starwave is a DMR monitoring and recording application for Pi-Star/MMDVM. It
copies Homebrew DMRD traffic over an SSH stream, independently decodes both
slots, and provides call logs, audio playback, transcription and Chinese
translation in a compact Arco Design radio panel.

Repository: [Minorli/mmdvm-dmr-monitor](https://github.com/Minorli/mmdvm-dmr-monitor).
Configure the radio and translation provider after deployment; private provider
addresses, credentials and captured calls are not included in the repository.

## Run with Docker

```sh
cp .env.example .env
docker compose up -d --build
```

Open `http://127.0.0.1:8788`. First launch is Web-only standby. Use Connection
Settings to enter the device address, SSH port, username and password; verify
the host fingerprint before trusting a new device. Stop listening and use Translation Settings to
configure the API base URL, model, Responses or Chat Completions mode, API key,
and whether translation is enabled. Keys are write-only; the panel reports only
whether a key is configured. A new provider requires a new key to enable
translation; disabling translation allows the old key to be cleared.
Saved Web settings override deployment defaults and
survive container recreation. Recording and transcription also work with
translation disabled.

Existing key authentication also works with the SSH overlay and a dedicated
identity. Environment variables or `.env` provide initial defaults; API and
control tokens can also be mounted as read-only file secrets.
See the deployment guide for the exact overlays and environment variables.

The operator explicitly starts and stops listening. The last choice is
persisted: a supervised restart resumes a started session, and a manually
stopped session stays stopped. Stop closes capture and speech processes while
leaving history and playback available.

SQLite jobs, call metadata, audio, call-directory data, and Web connection and
translation settings live in `/data`. This includes saved SSH passwords and API
keys, so complete data backups must remain private;
model downloads live in `/models`. Containers can be replaced without losing
these volumes. Never use `down -v` unless deleting the stored data is intended.
Audio is organized by date, with stable UUID playback URLs. Tasks use leases,
bounded retries and transactional stage transitions. Active audio has atomic
one-second recovery snapshots; the latest unsynchronized interval may be lost
on a hard exit.

Recording retention is disabled by default. Optional expiration only removes
finished, nonpending audio and keeps text. Verified offline backup/restore,
audit and explicit failed-job retry are available through
`python -m backend.maintenance`.

## Development

```sh
uv sync --frozen
./scripts/build-codec.sh
npm --prefix web ci
./scripts/check.sh
./scripts/check.sh --docker
```

Supported runtime: Linux and Python 3.12, with a software DMR codec built from
pinned sources. Tests must use temporary storage and fake external services;
never control a real systemd service from a deployment test.

Transcripts and translations are automated tool output, not a guarantee of
speech accuracy. The capture input currently supports IPv4 Homebrew DMRD on
UDP 62031. The decoder cannot decrypt encrypted DMR speech.

Application code is MIT licensed. Dependencies and bundled assets retain their
own licenses; see [NOTICE](NOTICE.md), [CONTRIBUTING](CONTRIBUTING.md) and
[SECURITY](SECURITY.md).
