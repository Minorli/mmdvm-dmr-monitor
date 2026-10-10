FROM python:3.12-slim-bookworm
# A test-only SSH endpoint. Credentials are intentionally synthetic and it is
# run only on a newly-created internal Docker network without published ports.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server sudo \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 2000 --create-home --shell /bin/sh listener \
    && printf '%s\n' 'listener:only-for-isolated-ssh-test' | chpasswd \
    && printf '%s\n' 'listener ALL=(root) NOPASSWD: /usr/local/bin/python3' > /etc/sudoers.d/listener \
    && chmod 0440 /etc/sudoers.d/listener \
    && mkdir -p /run/sshd \
    && rm -f /etc/ssh/ssh_host_* \
    && printf '%s\n' 'Port 22' 'ListenAddress 0.0.0.0' \
       'HostKey /etc/ssh/ssh_host_ed25519_key' 'PasswordAuthentication yes' \
       'PubkeyAuthentication no' 'KbdInteractiveAuthentication no' \
       'PermitRootLogin no' 'UsePAM no' 'LogLevel ERROR' \
       > /etc/ssh/sshd_config
CMD ["sh", "-c", "ssh-keygen -A >/dev/null 2>&1 && exec /usr/sbin/sshd -D -e"]
