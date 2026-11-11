#!/bin/bash
# Removes the NetXWheel service and the netxwheel command. Your settings and this folder stay;
# delete them yourself if you want (the folder, and ~/.config/netxwheel).
set -e
sudo systemctl disable --now netxwheel 2>/dev/null || true
sudo rm -f /etc/systemd/system/netxwheel.service /usr/local/bin/netxwheel
sudo systemctl daemon-reload
echo "NetXWheel removed. Settings kept in ~/.config/netxwheel"
