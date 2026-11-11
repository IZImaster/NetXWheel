#!/bin/bash
# NetXWheel installer for Linux (Raspberry Pi OS, Debian, Ubuntu...).
# Run it as your normal user, from the NetXWheel folder:   ./install.sh
# It is safe to run again (for example after moving the folder); your settings are kept.
set -e

REPO="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
if [ "$(id -u)" = "0" ] && [ -z "$SUDO_USER" ]; then
    echo "Please run this as your normal user (not as root): ./install.sh"
    exit 1
fi
say() { printf '\n== %s\n' "$*"; }

say "NetXWheel: installing from $REPO for user $RUN_USER"

say "1/6 Python and evdev"
if ! command -v python3 >/dev/null; then
    sudo apt-get update && sudo apt-get install -y python3
fi
if ! python3 -c "import evdev" 2>/dev/null; then
    if command -v apt-get >/dev/null; then
        sudo apt-get install -y python3-evdev
    else
        echo "Please install python3-evdev with your system's package manager, then run ./install.sh again."
        exit 1
    fi
fi
echo "ok"

say "2/6 Permission to read the wheel (the 'input' group)"
if id -nG "$RUN_USER" | grep -qw input; then
    echo "ok"
else
    sudo usermod -aG input "$RUN_USER"
    echo "added $RUN_USER to the input group"
fi

say "3/6 Stopping any older wheel server"
if systemctl list-unit-files 2>/dev/null | grep -q '^wheel-server.service'; then
    sudo systemctl disable --now wheel-server 2>/dev/null || true
    echo "stopped and switched off the old wheel-server service"
fi
if pkill -f "(^|[ /])wheel_server\.py" 2>/dev/null; then
    echo "stopped the old wheel_server.py"
fi
if systemctl is-active --quiet netxwheel 2>/dev/null; then
    sudo systemctl stop netxwheel
fi
echo "ok"

say "4/6 Your settings"
SETTINGS="$(sudo -u "$RUN_USER" python3 -c "import sys; sys.path.insert(0, '$REPO'); from netxwheel import settings; print(settings.ensure())")"
echo "$SETTINGS (kept if it was already there)"

say "5/6 The netxwheel command"
sudo tee /usr/local/bin/netxwheel >/dev/null <<EOF
#!/bin/sh
exec python3 "$REPO/netxwheel_cli.py" "\$@"
EOF
sudo chmod 755 /usr/local/bin/netxwheel
echo "ok: try  netxwheel help"

say "6/6 Starting by itself at boot (the netxwheel service)"
sudo tee /etc/systemd/system/netxwheel.service >/dev/null <<EOF
[Unit]
Description=NetXWheel - steering wheel over the network
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
SupplementaryGroups=input
WorkingDirectory=$REPO
ExecStart=/usr/bin/env python3 -u $REPO/netxwheel_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now netxwheel
sleep 3

say "Done"
if systemctl is-active --quiet netxwheel; then
    echo "The server is running and starts by itself whenever this machine starts."
    journalctl -u netxwheel -n 5 --no-pager -o cat || true
else
    echo "The server did not start. Its messages:"
    journalctl -u netxwheel -n 20 --no-pager -o cat || true
fi
cat <<'EOF'

Next:
  netxwheel status      is the wheel there? (plug it in and switch it on first)
  netxwheel settings    see and change the settings
  netxwheel help        everything else
In the game, switch on the network wheel; it finds this machine by itself.
EOF
