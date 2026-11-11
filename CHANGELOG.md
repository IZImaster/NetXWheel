# Changes

## 1.0.0
- Protocol version 1 (PROTOCOL.md): finding servers by name, several wheels per server and several
  games at once (one per wheel), server and wheel passwords, signed sessions, game-neutral forces
  (constant, spring, damper, friction, rumble), up to 64 buttons with names, handbrake, extra axes.
- Wheels can be unplugged and plugged back in: they keep their number, and the game is told.
- New commands: `netxwheel name`, `password`, `kick`; `center`, `reset` and `buzz` take a wheel number.
- Each wheel's centre and password are kept in wheels.ini.
- `netxwheel/client.py`: a client in Python; `netxwheel test` lists servers and wheels.
- Replaces the 0.1 prototype and its GT3W protocol.

## 1.0.0
- First release. Server for Linux / Raspberry Pi (evdev) and Windows (SDL, not yet tested on a real wheel).
- Protocol version 1 (PROTOCOL.md), frozen.
- Wheel profiles (profiles/*.ini) and `netxwheel make-profile`; a generic mode for wheels without one.
- Settings file with `netxwheel set`, applied without a restart.
- Steering scaled to `steer_degrees` (default 360) without changing the wheel's own rotation.
- While no game is connected: centring force, warning vibration near the end stop, `netxwheel center`.
- Installer with a service that starts at boot (Linux), install.bat and start.bat (Windows).
