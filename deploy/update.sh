#!/bin/sh
# Update the live server. Run on the VPS:  sudo sh /opt/beacon/deploy/update.sh
set -e
cd /opt/beacon && sudo -u beacon git pull --ff-only && sudo -u beacon .venv/bin/pip install -q . && systemctl restart beacon && echo "Beacon updated."
