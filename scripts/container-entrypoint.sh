#!/usr/bin/env sh
set -eu
umask 077

if [ "$(id -u)" -eq 0 ]; then
    printf 'Starwave must run as a non-root user. Set APP_UID and APP_GID when building.\n' >&2
    exit 1
fi

for host_directory in "${HOST_DATA_DIR:-}" "${HOST_MODEL_CACHE_DIR:-}"; do
    case "$host_directory" in
        ""|/*) ;;
        *)
            printf 'HOST_DATA_DIR and HOST_MODEL_CACHE_DIR must be absolute paths.\n' >&2
            exit 1
            ;;
    esac
done

for directory in "${DATA_DIR:-/data}" "${CACHE_DIR:-/models/cache}"; do
    if ! mkdir -p "$directory" || [ ! -w "$directory" ]; then
        printf 'Cannot write %s. Match volume ownership to APP_UID/APP_GID.\n' "$directory" >&2
        exit 1
    fi
done

exec "$@"
