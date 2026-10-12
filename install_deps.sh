#!/bin/sh
# Create the Python virtual env and install the dependencies.
# Idempotent: it does nothing when the venv already matches requirements.txt
# and the Python version, so it can run at every application start.
set -eu
cd "$(dirname "$0")"

VENV_DIR=${VENV_DIR:-venv}
PYTHON=${PYTHON:-python3}
STAMP="$VENV_DIR/.requirements.stamp"

# Fingerprint: requirements + interpreter version
expected="$(sha256sum requirements.txt | cut -d' ' -f1) $($PYTHON --version 2>&1)"

if [ -x "$VENV_DIR/bin/python" ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$expected" ]; then
    echo "Python dependencies up to date"
    exit 0
fi

echo "Installing Python dependencies in $VENV_DIR (first start or requirements changed)..."
# --clear empties the directory but keeps it, so it also works on a mounted volume
"$PYTHON" -m venv --clear "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt
echo "$expected" > "$STAMP"
echo "Python dependencies installed"
