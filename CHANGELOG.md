# Changes

## 1.0.0
- First release. Server for Linux / Raspberry Pi (evdev) and Windows (SDL, not yet tested on a real wheel).
- Protocol version 1 (PROTOCOL.md), frozen.
- Wheel profiles (profiles/*.ini) and `netxwheel make-profile`; a generic mode for wheels without one.
- Settings file with `netxwheel set`, applied without a restart.
- Steering scaled to `steer_degrees` (default 360) without changing the wheel's own rotation.
- While no game is connected: centring force, warning vibration near the end stop, `netxwheel center`.
- Installer with a service that starts at boot (Linux), install.bat and start.bat (Windows).
