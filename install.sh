#!/bin/bash

# Enable Python Virtual Env and Activate
python3 -m venv venv
source ./venv/bin/activate

# Install Python packages
pip install -r requirements.txt

chmod +x start.sh status.sh stop.sh

mkdir -p log