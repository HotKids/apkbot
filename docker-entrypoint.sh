#!/bin/sh
# Run the bot as an unprivileged user. /data written by earlier root-run
# releases is handed over first; if that is impossible (e.g. a read-only or
# squashed mount), keep the old behaviour instead of failing to start.
set -e
if [ "$(id -u)" = 0 ]; then
    if chown -R apkdl:apkdl /data 2>/dev/null; then
        exec setpriv --reuid=apkdl --regid=apkdl --init-groups "$@"
    fi
    echo "apkbot: cannot hand /data to user apkdl; running as root" >&2
fi
exec "$@"
