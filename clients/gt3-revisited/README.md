# Client example: GT3 Revisited (C++, SDL3)

> **Note:** this copy still speaks the 0.1 prototype protocol (GT3W). It is being updated to protocol
> version 1; until then, read it for the structure (virtual SDL wheel, sockets) and
> [netxwheel/client.py](../../netxwheel/client.py) for the protocol itself.

`net_wheel.h` and `net_wheel.cpp` are the NetXWheel client from GT3 Revisited
([its fork with the network wheel](https://github.com/IZImaster/GT3-revisited-Steering-Wheel-FFB/tree/netwheel),
files `src/input/net_wheel.*`). Copied here as a reference for adding NetXWheel to another game.

What it does:

- Opens a UDP socket and sends HELLO once a second, to `255.255.255.255:27920` (auto) or to a set
  address. The first server that answers is used from then on.
- On HELLO_ACK it attaches an **SDL virtual joystick** with 4 axes (steering, gas, brake, clutch),
  19 buttons and a hat, so the rest of the game sees an ordinary wheel: bindings, menus and force
  settings work unchanged.
- On STATE it sets the virtual joystick's axes, buttons and hat (dropping out-of-order packets).
- `send_force()` sends the game's spring and steering force (FFB) every frame while racing.
- If nothing arrives for 2 seconds the virtual wheel is detached (like unplugging a wheel); it comes
  back by itself when the server answers again.

The game-specific parts are small: the button order of the virtual joystick (`kButtonMap`), the
logging macro and the namespace. In the game, `InputManager` creates it from `config.ini`
(`[netwheel] enabled / host / port`) and Settings > Controls > Network wheel.

Licence: GT3 Revisited's MIT licence, in [LICENSE](LICENSE) here.
