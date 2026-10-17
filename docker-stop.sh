#!/usr/bin/env bash
# Stop the container of webpush (README.md > Operations), as the owner of the installation:
#
#     /srv/webpush/docker-stop.sh
#
# A stopped container stays stopped also after a reboot of the server (--restart unless-stopped), until
# docker-start.sh. The app waits for the sendings running within the stop timeout of the container
# (70 seconds, docker-start.sh), then Docker kills it.
set -euo pipefail

NAME=webpush

if ! docker container inspect "$NAME" >/dev/null 2>&1; then
    echo "No container $NAME" >&2
    exit 1
fi
if [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" != true ]; then
    echo "$NAME is already stopped"
    exit 0
fi
echo "Stop of the container $NAME"
docker stop "$NAME" >/dev/null
echo "$NAME stopped: start it again with $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker-start.sh"
