# The NetXWheel protocol, version 2 (draft for review)

A game (the **client**) uses steering wheels plugged into another machine (the **server**) over the
local network. One server can have several wheels of different makes; one network can have several
servers; several games can each use their own wheel at the same time.

Status: **draft**. Version 1 is retired (servers speak only version 2).

## Contents

1. [Overview](#1-overview)
2. [Packets](#2-packets)
3. [Finding servers: DISCOVER and ANNOUNCE](#3-finding-servers-discover-and-announce)
4. [Connecting to a wheel: CONNECT, CHALLENGE, ACCEPT, REJECT](#4-connecting-to-a-wheel)
5. [Passwords and signing](#5-passwords-and-signing)
6. [Driving: STATE and FORCES](#6-driving-state-and-forces)
7. [Staying connected and leaving](#7-staying-connected-and-leaving)
8. [The wheel description](#8-the-wheel-description)
9. [Keeping it compatible](#9-keeping-it-compatible)
10. [A whole session, step by step](#10-a-whole-session-step-by-step)

## 1. Overview

- **UDP**, one message per datagram, binary numbers **little-endian**, text **UTF-8**.
- Server port **27920** (setting `port`). Everything uses this one port.
- Two kinds of messages:
  - **Setup messages** carry a small **JSON** object: finding servers, wheel lists, connecting,
    errors. New fields can be added at any time; readers ignore fields they don't know.
  - **Live messages** are compact **binary**: the wheel's state (100 a second) and the game's forces.
- A **session** is one game using one wheel. A server holds one session per wheel at most; a wheel
  in use stays with its game until the game leaves or stops answering (see 7).
- While a wheel has no session the server may play its own gentle forces on it (NetXWheel: a
  centring force and a warning vibration near the end stop). They stop when a session starts.

## 2. Packets

Every packet starts with this **16-byte header**:

| Offset | Field | Type | Notes |
|---|---|---|---|
| 0 | magic | 4 bytes | `NXW2` |
| 4 | type | u8 | see the table below |
| 5 | flags | u8 | bit 0: the packet ends with a 16-byte **signature** (see 5); other bits 0 |
| 6 | reserved | u16 | 0 |
| 8 | session | u32 | the session id from ACCEPT; 0 before that |
| 12 | seq | u32 | the sender's counter for this session, +1 per packet (see 5) |

Then the body, then (if flags bit 0) the 16-byte signature.

| Type | Name | Direction | Body | Signed |
|---|---|---|---|---|
| 1 | DISCOVER | game → server(s) | JSON | no |
| 2 | ANNOUNCE | server → game | JSON | no |
| 3 | CONNECT | game → server | JSON | no |
| 4 | CHALLENGE | server → game | JSON | no |
| 5 | ACCEPT | server → game | JSON | yes |
| 6 | REJECT | server → game | JSON | no |
| 16 | STATE | server → game | binary | yes |
| 17 | FORCES | game → server | binary | yes |
| 18 | KEEPALIVE | both | empty | yes |
| 19 | NOTICE | server → game | JSON | yes |
| 20 | BYE | both | empty | yes |

Keep each packet under **1200 bytes** (safe on Wi-Fi without fragmentation). A receiver drops
packets with the wrong magic, unknown types, bad signatures or old sequence numbers, silently.

## 3. Finding servers: DISCOVER and ANNOUNCE

The game sends **DISCOVER** to the broadcast address `255.255.255.255:27920` (and/or to addresses the
player typed), for example once when its menu opens and then every 2 seconds while the list is shown.
Asking connects to nothing.

```json
{"proto": 2, "client": "GT3 Revisited 1.4 (Quest 3)"}
```

Every server answers with **ANNOUNCE** to the address the DISCOVER came from:

```json
{
  "proto": 2,
  "server_id": "6f1c2a90e4b84c31",
  "name": "Living room Pi",
  "software": "NetXWheel 2.0.0",
  "password": true,
  "wheels": [
    {"index": 1, "vendor": "Logitech", "model": "G920 Driving Force",
     "rotation": 900, "steer_range": 360, "ffb": true, "direct_drive": false, "torque_nm": 2.2,
     "pedals": ["throttle", "brake", "clutch"], "shifter": "h6r",
     "password": false, "in_use": true, "in_use_by": "Quest 3 (192.168.1.20)"},
    {"index": 2, "vendor": "Moza", "model": "R5", "rotation": 1080, "steer_range": 540,
     "ffb": true, "direct_drive": true, "torque_nm": 5.5, "pedals": ["throttle", "brake"],
     "shifter": "none", "password": true, "in_use": false}
  ]
}
```

| Field | Meaning |
|---|---|
| `server_id` | stays the same across restarts (to recognise a server whose address changed) |
| `name` | the server's name (setting `name`) |
| `password` | the server needs its password (see 5) |
| `wheels[].index` | the wheel's number on this server, 1, 2, 3...: stays with the wheel while the server runs (the order they were found); a wheel that is unplugged keeps its number with `"connected": false` |
| `rotation` | the wheel's own rotation, lock-to-lock, degrees |
| `steer_range` | degrees lock-to-lock that the server maps to STATE steering -1..+1 (setting) |
| `ffb` | it can play forces |
| `direct_drive`, `torque_nm` | from its profile (`torque_nm` may be missing if unknown) |
| `pedals` | which of throttle, brake, clutch, handbrake it has |
| `shifter` | `none`, `h6r` (H-shifter 1-6 and reverse), `h7r`, `sequential` |
| `password` | this wheel needs its own password too |
| `in_use`, `in_use_by` | a game has it, and the name that game gave |
| `connected` | `false` if the wheel is unplugged right now (missing means true) |

A server answers each address at most **4 DISCOVERs a second** and ignores the rest, so it can't be
used to flood a third device with answers (ANNOUNCE is bigger than DISCOVER).

The full description of a wheel (button names, extra axes) comes in ACCEPT (see 8), to keep ANNOUNCE
small. The game shows the servers and their wheels, and the player picks one.

## 4. Connecting to a wheel

**1. CONNECT** (game → server):

```json
{"proto": 2, "wheel": 1, "client": "GT3 Revisited 1.4 (Quest 3)", "client_nonce": "9a41...(32 hex)"}
```

`client_nonce` is 16 random bytes in hex, new for every attempt.

**2. CHALLENGE** (server → game), always, with or without passwords:

```json
{"nonce": "d03b...(32 hex)", "salt": "51e0...(32 hex)", "iterations": 20000,
 "server_password": true, "wheel_password": false, "client_nonce": "9a41..."}
```

`salt` stays the same for this server (it changes only when a password changes); `nonce` is new
every time. The game asks the player for the passwords that are `true` (or uses saved ones).

**3. CONNECT again**, now with the proof (see 5):

```json
{"proto": 2, "wheel": 1, "client": "GT3 Revisited 1.4 (Quest 3)", "client_nonce": "9a41...",
 "nonce": "d03b...", "proof": "77c2...(64 hex)", "state_hz": 100}
```

`state_hz` (optional, 25..250, default 100) is how many STATE packets a second the game wants.

**4a. ACCEPT** (signed with the new session key):

```json
{"session": 2817364401, "wheel": { ...the wheel description, see 8... },
 "state_hz": 100, "timeout_ms": 3000, "force_timeout_ms": 300}
```

The header's `session` field carries the same number from here on.

The game **must check ACCEPT's signature** with the session key it worked out (see 5) and drop an
ACCEPT that doesn't match. With a password set, that proves the answer comes from the real server:
a device pretending to be the server can't sign it without knowing the password. Without any
password, any device could pretend to be the server; the game should say so to the player (for
example "no password: not checked") and remember the `server_id` it used before.

**4b. REJECT**:

```json
{"reason": "in_use", "message": "Wheel 1 is being used by Quest 3 (192.168.1.20)", "client_nonce": "9a41..."}
```

`client_nonce` repeats the one from the CONNECT being answered. The game **ignores a REJECT (and a
CHALLENGE) whose `client_nonce` isn't the one it just sent**, so a device that sends fake rejections
can't stop it connecting. CHALLENGE carries `client_nonce` the same way.

| reason | |
|---|---|
| `in_use` | another game has the wheel |
| `password` | a password is wrong or missing |
| `no_wheel` | no wheel with that number (or it is unplugged) |
| `proto` | the server doesn't speak this protocol version (the body also says `"proto": n`) |
| `busy` | too many attempts; try again later (`retry_ms`) |
| `nonce` | the CHALLENGE was too old (over 10 s) or used already: start again at 1 |

A server allows each address at most 5 failed password attempts a minute.

## 5. Passwords and signing

Passwords never travel over the network. Both sides work out the same keys:

```
secret      = server_password + "\n" + wheel_password    (an empty string for a password not set)
key         = PBKDF2-HMAC-SHA256(secret, salt, iterations, 32 bytes)
proof       = HMAC-SHA256(key, "proof" + nonce + client_nonce + wheel index as text)    -> 64 hex
session_key = HMAC-SHA256(key, "session" + nonce + client_nonce)
```

(`nonce` and `client_nonce` as their hex text; `+` joins text.) Without any passwords this still
gives every session its own key, so packets can't be mixed between sessions.

**Signing:** a signed packet ends with the first 16 bytes of
`HMAC-SHA256(session_key, header + body)`. Every packet after ACCEPT is signed.

**Sequence numbers:** each side counts its own packets in a session from 1. A receiver drops a
packet whose `seq` is not higher than the highest it has accepted (so old or replayed packets
are ignored; a lost packet just leaves a gap).

What this protects: other games and devices on the network can't use a wheel without the
password, take over a session, inject forces or fake the wheel's controls (every live packet is
signed), replay old packets, block a connection with fake rejections (4), or, with a password,
pretend to be the server (4).

What it doesn't: the wheel's data isn't **encrypted**, so others on the network could read it
(where the wheel points, which buttons are pressed). Nobody can *change* it, though. Nothing can
stop someone **flooding** the network or jamming the Wi-Fi. It isn't meant for use over the internet.
Encryption could be added later as an option (a new capability, see 9) without breaking anything.

## 6. Driving: STATE and FORCES

### STATE (server → game), `state_hz` times a second

| Offset | Field | Type | Notes |
|---|---|---|---|
| 0 | ms | u32 | the server's milliseconds since the session started |
| 4 | steering | f32 | -1 full left .. +1 full right, over `steer_range` (held at ±1 beyond) |
| 8 | throttle | f32 | 0 released .. 1 floored |
| 12 | brake | f32 | 0 .. 1 |
| 16 | clutch | f32 | 0 .. 1 (0 if none) |
| 20 | handbrake | f32 | 0 .. 1 (0 if none) |
| 24 | buttons | u64 | bit mask, see the button table (8) |
| 32 | dpad | u8 | bits: 1 up, 2 right, 4 down, 8 left |
| 33 | gear | i8 | -1 reverse, 0 neutral, 1..7 (H-shifter; 0 without one) |
| 34 | status | u8 | bit 0: forces are playing; bit 1: the wheel is unplugged (all values at rest) |
| 35 | extra_count | u8 | how many extra axes follow |
| 36 | extra axes | f32 × extra_count | in the order the wheel description lists them (8) |

36 bytes plus 4 per extra axis.

### FORCES (game → server), whenever they change, and at least every 100 ms while racing

| Offset | Field | Type | Notes |
|---|---|---|---|
| 0 | flags | u8 | bit 0: forces on (0: the wheel goes limp, as in menus) |
| 1 | reserved | 3 bytes | 0 |
| 4 | constant | f32 | -1 .. +1, steering force, positive turns the wheel right |
| 8 | spring | f32 | 0 .. 1, centring strength |
| 12 | spring_center | f32 | -1 .. +1, where the spring pulls to (0 = straight; in steering units) |
| 16 | damper | f32 | 0 .. 1, resistance to turning quickly |
| 20 | friction | f32 | 0 .. 1, resistance to turning at all |
| 24 | rumble | f32 | 0 .. 1, vibration strength (bumps, ABS, kerbs) |
| 28 | rumble_hz | f32 | vibration frequency; 0 = the server's default (25 Hz) |

32 bytes. Strengths are fractions of what the wheel can do; the server applies its own overall
strength (the `gain` setting) and the wheel's limits on top. A wheel that can't play one kind of
force skips it (the wheel description says which it has). If FORCES with "forces on" stop arriving
for `force_timeout_ms` (300 ms), the server stops the forces: the wheel goes limp until the next one.

## 7. Staying connected and leaving

- The game sends **KEEPALIVE** at least once a second when it sends no FORCES (in menus);
  the server answers each KEEPALIVE with a KEEPALIVE.
- No packet from the game for `timeout_ms` (3 s): the server ends the session and the wheel is
  free again. The game, hearing nothing for 2 s, should treat the wheel as gone and try to connect
  again (from CONNECT).
- **BYE** (either side) ends the session at once.
- **NOTICE** (server → game) tells the game about a change, without ending the session:

```json
{"event": "unplugged"}                   the wheel was unplugged (STATE goes on with status bit 1)
{"event": "plugged"}                     it is back
{"event": "changed", "wheel": {...}}     its description changed (e.g. steer_range setting)
{"event": "kicked", "message": "..."}    the server's owner ended this session (followed by BYE)
{"event": "shutdown"}                    the server is stopping (followed by BYE)
```

## 8. The wheel description

Sent in ACCEPT (and NOTICE "changed"). Everything in the ANNOUNCE wheel entry, plus:

```json
{
  "index": 1, "vendor": "Logitech", "model": "G920 Driving Force", "rotation": 900, "steer_range": 360,
  "ffb": true, "direct_drive": false, "torque_nm": 2.2,
  "forces": ["constant", "spring", "damper", "friction", "rumble"],
  "pedals": ["throttle", "brake", "clutch"], "shifter": "h6r",
  "buttons": [
    {"bit": 0, "role": "a", "label": "A"}, {"bit": 4, "role": "paddle_right", "label": "Right paddle"},
    {"bit": 32, "role": "extra1", "label": "Dial +"}
  ],
  "dpad": true,
  "extra_axes": [{"role": "dial", "label": "Brake bias"}]
}
```

`buttons` lists only the buttons this wheel has. Standard roles and their bits (a game can bind by
role and show the label):

| Bit | role | Bit | role |
|---|---|---|---|
| 0 | `a` (cross) | 10 | `home` (Xbox / PS / logo) |
| 1 | `b` (circle) | 11 | `l2` |
| 2 | `x` (square) | 12 | `r2` |
| 3 | `y` (triangle) | 13 | `plus` |
| 4 | `paddle_right` (shift up) | 14 | `minus` |
| 5 | `paddle_left` (shift down) | 15 | `enter` (dial press) |
| 6 | `menu` (start / options) | 16 | `shift_up` (sequential shifter) |
| 7 | `view` (select / share) | 17 | `shift_down` (sequential shifter) |
| 8 | `rsb` (R3) | 18..31 | reserved for more standard roles |
| 9 | `lsb` (L3) | 32..63 | `extra1` .. `extra32`, anything else |

H-shifter gears are in STATE `gear`, not in `buttons`.

## 9. Keeping it compatible

- JSON messages: fields may be **added** any time; readers ignore unknown fields.
  A field's meaning never changes.
- Binary messages: fields may be **added at the end**; readers accept longer packets and ignore
  the rest. Reserved bytes and unused flag bits are 0.
- New message types may be added; readers ignore types they don't know.
- Only a change that can't be made that way gets a new version (`NXW3`), and the `proto`
  field in DISCOVER/CONNECT lets a server answer each game in a version it speaks.

## 10. A whole session, step by step

```
game                                        server "Living room Pi"
 | DISCOVER (broadcast)  ------------------>  |
 | <------------------  ANNOUNCE (2 wheels)   |   the player picks wheel 1
 | CONNECT wheel 1, client_nonce  --------->  |
 | <---------------  CHALLENGE nonce, salt    |   asks for the server password if needed
 | CONNECT wheel 1, nonce, proof  --------->  |
 | <--------------- ACCEPT session, wheel     |   (signed) idle forces stop
 | <--------------- STATE x100/s              |
 | FORCES (racing) ------------------------>  |   forces play on the wheel
 | KEEPALIVE (menus) ---------------------->  |
 | BYE  ----------------------------------->  |   the wheel is free again
```

Example code: [clients/](clients/) (a game client in C++) and [netxwheel/testclient.py](netxwheel/testclient.py)
(Python), once version 2 is built.
