#!/usr/bin/env bash
# Build the image and (re)create the container of webpush (README.md > Install), as the owner of the installation
# (on a server the user webpush, on your computer yourself):
#
#     /srv/webpush/app/docker-start.sh
#
# One installation serves all the sites. A running container is replaced: the sendings running are stopped
# (their messages end as failed).
set -euo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
NAME=webpush
if [ ! -f "$DIR/.env" ]; then
    echo "$DIR/.env not found: see README.md > Install" >&2
    exit 1
fi
PORT=$(sed -n 's/^WEB_PORT=//p' "$DIR/.env" | tail -1)
PORT=${PORT:-8080}

cd "$DIR"
mkdir -p log venv

echo "Build of the image $NAME"
docker build -q -t "$NAME" --build-arg APP_UID="$(id -u)" --build-arg APP_GID="$(id -g)" . >/dev/null

if docker container inspect "$NAME" >/dev/null 2>&1; then
    # stop first (SIGTERM: the app waits for the sendings running), then remove
    echo "Stop and removal of the container $NAME"
    docker stop "$NAME" >/dev/null
    docker rm "$NAME" >/dev/null
fi

echo "Start of the container $NAME"
docker run -d --name "$NAME" \
    --restart unless-stopped \
    --stop-timeout 70 \
    --network host \
    --env-file "$DIR/.env" \
    -e TZ=Europe/Rome \
    --cpus 1 --memory 512m --memory-swap 512m \
    -v "$DIR/log:/app/log" \
    -v "$DIR/venv:/app/venv" \
    "$NAME" >/dev/null

# the first start installs the Python dependencies: a few minutes
echo -n "Waiting for the web app on 127.0.0.1:$PORT "
for _ in $(seq 120); do
    # a failing app is restarted by Docker (--restart): stopped or restarted means failed
    if [ "$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$NAME")" != "true 0" ]; then
        echo
        echo "The container stopped or restarted: its last output" >&2
        docker logs --tail 30 "$NAME" >&2
        exit 1
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
        echo
        echo "$NAME started: web app on 127.0.0.1:$PORT"
        echo "Logs: docker logs -f $NAME (Ctrl-C to quit), tail -f $DIR/log/app.log"
        exit 0
    fi
    echo -n .
    sleep 5
done
echo
echo "The web app doesn't answer after 10 minutes: see docker logs $NAME" >&2
exit 1
