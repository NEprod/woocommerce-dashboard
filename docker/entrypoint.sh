#!/bin/sh
set -eu

fail() {
    printf 'entrypoint: %s\n' "$1" >&2
    exit 64
}

warn() {
    printf 'entrypoint: warning: %s\n' "$1" >&2
}

if [ "${PUID+x}" = "x" ]; then
    RUNTIME_PUID=${PUID}
else
    RUNTIME_PUID=100
fi

if [ "${PGID+x}" = "x" ]; then
    RUNTIME_PGID=${PGID}
else
    RUNTIME_PGID=100
fi

if [ "${UMASK+x}" = "x" ]; then
    RUNTIME_UMASK=${UMASK}
else
    RUNTIME_UMASK=002
fi

case "${RUNTIME_PUID}" in
    ""|0|*[!0-9]*) fail "PUID must be a non-zero numeric UID" ;;
esac
case "${RUNTIME_PGID}" in
    ""|0|*[!0-9]*) fail "PGID must be a non-zero numeric GID" ;;
esac
if [ "${RUNTIME_PUID}" -gt 2147483647 ]; then
    fail "PUID must be a non-zero numeric UID"
fi
if [ "${RUNTIME_PGID}" -gt 2147483647 ]; then
    fail "PGID must be a non-zero numeric GID"
fi
case "${#RUNTIME_UMASK}:${RUNTIME_UMASK}" in
    3:*|4:*) ;;
    *) fail "UMASK must be three or four octal digits" ;;
esac
case "${RUNTIME_UMASK}" in
    *[!0-7]*) fail "UMASK must be three or four octal digits" ;;
esac

PUID=${RUNTIME_PUID}
PGID=${RUNTIME_PGID}
UMASK=${RUNTIME_UMASK}
export PUID PGID UMASK

if [ "${1:-}" = "--validate-config" ]; then
    printf 'PUID=%s PGID=%s UMASK=%s\n' "${PUID}" "${PGID}" "${UMASK}"
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    fail "startup preparation requires root; the application is dropped to PUID:PGID before launch"
fi

target_group=$(getent group "${PGID}" | cut -d: -f1 || true)
current_group=$(id -gn app)
current_gid=$(id -g app)

if [ "${current_gid}" != "${PGID}" ]; then
    if [ -n "${target_group}" ] && [ "${target_group}" != "${current_group}" ]; then
        usermod --gid "${target_group}" app
    else
        groupmod --gid "${PGID}" app
        if [ "$(id -g app)" != "${PGID}" ]; then
            usermod --gid app app
        fi
    fi
fi

target_user=$(getent passwd "${PUID}" | cut -d: -f1 || true)
if [ -n "${target_user}" ] && [ "${target_user}" != "app" ]; then
    fail "PUID is already assigned to another image account"
fi
if [ "$(id -u app)" != "${PUID}" ]; then
    usermod --uid "${PUID}" app
fi

if ! mkdir -p /app/instance/backups; then
    fail "could not prepare /app/instance; ensure the mount is read/write for container startup"
fi
mkdir -p /catalogue /output
if ! chown -R "${PUID}:${PGID}" /app/instance; then
    fail "could not assign /app/instance to configured PUID ${PUID} and PGID ${PGID}"
fi

for runtime_path in /catalogue /output; do
    if ! chown "${PUID}:${PGID}" "${runtime_path}" 2>/dev/null; then
        warn "could not adjust ${runtime_path}; configure the mounted share for UID ${PUID} and GID ${PGID}"
    fi
done

umask "${UMASK}"

# The probe variable expands in the deliberately separate non-root shell.
# shellcheck disable=SC2016
if ! gosu "${PUID}:${PGID}" sh -c '
    probe=$(mktemp /app/instance/.write-check.XXXXXX) || exit 1
    rm -f "${probe}"
'; then
    fail "/app/instance is not writable by configured PUID ${PUID} and PGID ${PGID}"
fi

for runtime_path in /catalogue /output; do
    # The probe variables expand in the deliberately separate non-root shell.
    # shellcheck disable=SC2016
    if ! gosu "${PUID}:${PGID}" sh -c '
        probe=$(mktemp "${1}/.write-check.XXXXXX") || exit 1
        rm -f "${probe}"
    ' sh "${runtime_path}" 2>/dev/null; then
        warn "${runtime_path} is not writable by configured PUID ${PUID} and PGID ${PGID}; setup may continue, but scanning requires read/write access"
    fi
done

printf 'entrypoint: starting application as UID %s GID %s with umask %s\n' \
    "${PUID}" "${PGID}" "${UMASK}"
exec gosu "${PUID}:${PGID}" "$@"
