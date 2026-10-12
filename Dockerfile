# webpush application image.
# The image holds only Python and the code: the dependencies are installed by
# install_deps.sh at the first start, into the venv volume (/app/venv).
# The database is the MariaDB server of the host (not a container): run with
# --network host and DB_HOST=127.0.0.1 (see README.md > Install).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Rome \
    PATH=/app/venv/bin:$PATH

# tzdata: the schedule slots run in local time (TZ)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# User of the application: build with the uid / gid of the owner of the data directories on the host
# (--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)), so no chown is needed
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --non-unique --gid "$APP_GID" app \
    && useradd --non-unique --uid "$APP_UID" --gid "$APP_GID" --create-home app

WORKDIR /app

COPY --chown=app:app . .

# Log directory and venv, mounted as volumes on the host
RUN mkdir -p log venv \
    && chown -R app:app /app

USER app

EXPOSE 8080

# Long start period: the first start downloads the dependencies
HEALTHCHECK --interval=60s --timeout=5s --start-period=10m --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.getenv('WEB_PORT', '8080'), timeout=4)"

CMD ["sh", "-c", "./install_deps.sh && exec python app.py"]
