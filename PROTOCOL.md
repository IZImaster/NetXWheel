# The GT3W prototype protocol (NetXWheel 0.1)

> This is the development prototype's protocol. NetXWheel 1.0 replaces it with **protocol version 1**
> (several wheels and servers, passwords): see the `protocol-v2` branch. Don't build new games on this one.

This is the fixed part of NetXWheel: anything that speaks it works with any NetXWheel server and any
wheel the server supports. A game (the **client**) talks to the **server** next to the wheel.


A complete client in C++ is in [clients/gt3-revisited](clients/gt3-revisited) (about 260 lines),
and a small one in Python is [netxwheel/testclient.py](netxwheel/testclient.py).

## Basics

- UDP, one message per datagram, all numbers **little-endian**.
- Server port: **27920**.
- Finding the server: the client sends HELLO to the broadcast address `255.255.255.255:27920` and
  uses the first server that answers. It can also send to a known IPv4 address instead.

## Header (8 bytes, every message)

| Field | Type | Notes |
|---|---|---|
| magic | 4 bytes | `GT3W` (historical: the protocol began in GT3 Revisited; fixed forever) |
| version | u8 | the sender's protocol version: `1` |
| type | u8 | 1 HELLO, 2 HELLO_ACK, 3 STATE, 4 FFB, 5 BYE |
| seq | u16 | the sender's counter; a client drops a STATE older than the last one it saw (compare as signed 16-bit differences, so it wraps) |

## Session

1. The client sends **HELLO** about once a second (it is also the keep-alive).
   Payload: u8 client capabilities, bit 0 = wants force feedback.
2. The server answers **HELLO_ACK** to the address the HELLO came from (35 bytes after the header):
   u8 capabilities (bit 0 force feedback, bit 1 H-shifter), u16 steering range in degrees
   (lock-to-lock, what full left to full right means), 32 bytes device name (UTF-8, zero-padded).
3. While a client is connected the server sends **STATE** 100 times a second.
4. The client sends **FFB** every frame while it wants forces (while racing). If the server gets
   none for 300 ms it stops all forces: the wheel goes limp, as in a game's menus.
5. If the server hears nothing at all for 3 seconds it forgets the client. A client sends **BYE**
   when it stops. A client that hears nothing for 2 seconds should treat the wheel as unplugged.
6. One client at a time: a HELLO from a new address takes over.
7. A client that receives a version it does not support ignores the message (and may say so once).

While no client is connected the server may play its own forces (NetXWheel: a gentle centring force
and a warning vibration near the wheel's end stop). They stop when a client connects.

## STATE (server to client), 28 bytes after the header

| Field | Type | Notes |
|---|---|---|
| ms | u32 | the server's milliseconds since it started |
| steering | f32 | -1 full left .. +1 full right (full lock as in HELLO_ACK's range) |
| throttle | f32 | 0 released .. 1 floored |
| brake | f32 | 0 .. 1 |
| clutch | f32 | 0 .. 1 (0 if the wheel has none) |
| buttons | u32 | bit mask, see below |
| hat_x | i8 | D-pad: -1 left, 0, +1 right |
| hat_y | i8 | D-pad: -1 up, 0, +1 down |
| gear | i8 | H-shifter: -1 reverse, 0 neutral, 1..6 |
| pad | u8 | 0 |

**Buttons** (bit: meaning). The server's wheel profiles put each wheel's buttons on these:

| Bit | Button | Bit | Button |
|---|---|---|---|
| 0 | A (cross) | 9 | left stick / L3 |
| 1 | B (circle) | 10 | home (Xbox / PS / logo) |
| 2 | X (square) | 11 | shifter reverse |
| 3 | Y (triangle) | 12..17 | shifter gears 1..6 |
| 4 | right paddle (shift up) | 18..31 | extra buttons 1..14 (anything else the wheel has) |
| 5 | left paddle (shift down) | | |
| 6 | menu / start / options | | |
| 7 | view / select / share | | |
| 8 | right stick / R3 | | |

## FFB (client to server), 37 bytes after the header

The first eight fields describe what the car is doing, for servers that want to make their own effects.
The last two are the forces the game would play on a wheel plugged into it; NetXWheel plays those.

| Field | Type | Notes |
|---|---|---|
| speed_kmh | f32 | |
| brake | f32 | 0..1 |
| abs_level | f32 | 0..1, ABS working |
| steer_force | f32 | -1..1, positive turns the wheel right |
| jolt | f32 | 0..1, an impact this frame |
| traction | f32 | 0..1, wheel spin / loss of grip |
| understeer | f32 | 0..1 |
| oversteer | f32 | 0..1 |
| flags | u8 | bit 0: wheel force is switched on in the game |
| spring | i16 | centring spring, 0..32767 |
| constant | i16 | steering force, -32767..32767, positive pushes the wheel right |

The server applies its own overall strength (the `gain` setting) on top.

## Keeping it compatible

- Version 1 messages keep exactly these fields, sizes and meanings.
- New things may be **added at the end** of a message, announced by a **new capability bit**.
  Readers must accept messages longer than they expect and ignore what they don't know.
- The unused bits of the capability bytes, `flags` and `pad` are 0 and reserved for that.
- Only a change that cannot be made this way gets a version 2, and servers would then keep
  answering version 1 clients in version 1.
- Proposals: open an issue on GitHub titled "Protocol: ...".
