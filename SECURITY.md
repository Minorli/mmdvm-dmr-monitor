# Security

Keep deployments on a trusted network or behind an authenticated HTTPS reverse
proxy. Compose publishes on loopback by default. `CONTROL_TOKEN` protects start
and stop actions and connection/translation changes; it does not make the read-only dashboard or audio private.
Protect the whole site at the reverse proxy when deploying outside a trusted LAN.

Use file-based secrets for container deployments. Mount only the required SSH
identity and verified known-hosts file, never the entire home directory or Docker
socket. The application runs without privileged mode or local packet-capture
capabilities; packet capture occurs on the configured Pi-Star over SSH.

The capture account needs non-interactive SSH and permission to run the remote
collector. Password-based SSH is supported without putting the password in
process arguments or environment variables. Existing Pi-Star installations
commonly use `sudo -n python3` for capture permissions.
Prefer a dedicated, restricted key/account for a shared deployment and verify
host fingerprints out of band before adding them to known_hosts.

Do not include secrets, live recordings or full transcripts in public issue
reports. A minimal report should include application/image version, sanitized
configuration names, error category, and a synthetic reproduction.

For a security issue, contact the repository maintainers privately using the
hosting platform's security reporting channel when enabled. If it is not yet
enabled, request a private reporting route without posting exploit details or
credentials. This project does not currently claim a managed support SLA.

Web-saved API keys and SSH passwords are write-only settings stored in private
files under DATA_DIR. Full backups include the active secret generations and
must remain private. Never commit runtime settings, backups, HAR files, real
provider URLs or screenshots containing site configuration.
