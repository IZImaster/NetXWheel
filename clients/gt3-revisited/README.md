# Client example: GT3 Revisited (C++)

The NetXWheel client from GT3 Revisited
([its fork with the network wheel](https://github.com/IZImaster/GT3-revisited-Steering-Wheel-FFB/tree/netxwheel-v1),
`src/input/`), copied here as a reference for adding NetXWheel to another game.

- **`netxwheel.h` / `netxwheel.cpp`**: the whole protocol (version 1), no SDL: finding servers
  (DISCOVER / ANNOUNCE), connecting with passwords (CHALLENGE, PBKDF2 + HMAC proof, checking the
  signed ACCEPT, ignoring answers that aren't for us), the signed session (STATE, FORCES,
  KEEPALIVE, NOTICE, BYE), reconnecting by itself, and a rumble to identify the wheel.
  Plain sockets (Windows, Linux, macOS, Android). It uses the game's SHA-256 / HMAC / PBKDF2
  (`base/sha256.h`) and JSON reader (`formats/json.h`): swap in your own.
- **`net_wheel.h` / `net_wheel.cpp`**: the game side: the connected wheel becomes an **SDL virtual
  joystick** (SDL3 `SDL_AttachVirtualJoystick`, type wheel) so the rest of the game sees an
  ordinary wheel, and the race's forces go back to the server.

Usage, once per frame:

```cpp
netxwheel::Client c;
c.open();
c.discover();                    // every couple of seconds while a menu lists servers
c.poll();                        // reads answers: c.servers()
netxwheel::Target t;             // the player's choice
t.ip = s.ip; t.port = s.port; t.wheel = 1; t.server_id = s.server_id; t.server_password = "...";
c.connect(t);
// every frame:
c.poll();
netxwheel::WheelState st;
if (c.take_state(st)) { /* steering, pedals, buttons, gear */ }
c.send_forces({true, constant, spring});  // while racing
c.identify(2.0);                 // rumble to find the wheel
```

The game's tests (`tests/test_netxwheel.cpp` there) check its keys against the Python server's and
run a whole session against a small server.

Licence: GT3 Revisited's MIT licence, in [LICENSE](LICENSE) here.
