# NetXWheel

Use a force feedback steering wheel over the network: a small server (e.g. Raspberry Pi) and an open
protocol for games, for connecting a steering wheel with force feedback wirelessly to a VR
headset or phone on your local network.

```
 steering wheel ──USB──> small computer (Raspberry Pi, PC)  ~~~Wi-Fi~~~>  game (Quest, phone, PC)
                         running the NetXWheel server       <~~forces~~~
```

The server reads the wheel, its pedals and shifter, and sends them to the game many times a second.
The game sends back the forces (steering weight, bumps, ABS) and the server plays them on the wheel.

- **Several wheels** of any make on one server, numbered 1, 2, 3... Several games can each use
  their own wheel at the same time (four headsets, four wheels, one Pi).
- **Several servers** on one network: games list them by name, with every wheel's make, model,
  rotation, force feedback, direct drive and whether it is free.
- **Passwords**, for the whole server and for each wheel. A wheel a game is using stays its own
  until the game leaves.

**Games that support it:** GT3 Revisited on Meta Quest and PC,
in [this fork](https://github.com/IZImaster/GT3-revisited-Steering-Wheel-FFB/tree/netwheel) (being updated to
protocol version 1).
Want it in your game? See [For game developers](#for-game-developers).

## What you need

- A steering wheel with USB. Tested: **Logitech G920**. Others: see [Wheels](#wheels).
- A computer to plug it into, on the same network as the game:
  - a **Raspberry Pi** (a Pi 3B+ is plenty) or any **Linux** machine, or
  - a **Windows 10/11** PC (new, not tested on real wheels yet).

## Install on a Raspberry Pi / Linux

Open a terminal on the Pi (or connect with SSH) and paste:

```
sudo apt install -y git
git clone https://github.com/IZImaster/NetXWheel.git ~/NetXWheel
cd ~/NetXWheel && ./install.sh
```

It asks for your password, installs what it needs and starts the server. From then on the server
starts by itself every time the Pi starts. Plug the wheel in and check:

```
netxwheel status
```

**Update later:** `netxwheel update`. Your settings are kept.

## Install on Windows 10/11

1. Install Python from [python.org](https://www.python.org/downloads/). In its setup, tick
   **Add python.exe to PATH**.
2. For Logitech wheels, install **Logitech G HUB** (the wheel's driver, needed for the forces).
3. On this page click **Code > Download ZIP**, unzip it (e.g. to `Documents\NetXWheel`).
4. Open the `windows` folder and double-click **install.bat**.
5. To play: double-click **windows\start.bat** (or let install.bat start it at login).
   The first time, Windows asks about the firewall: click **Allow**.

## Playing

1. Wheel plugged in and switched on, server running (`netxwheel status` says "wheel ...").
2. In the game, switch the network wheel on, pick the server and the wheel from the list
   (type the passwords if they have any). In GT3 Revisited: **Settings > Controls > Network wheel**.
3. Drive.

While no game uses a wheel it gently centres itself and vibrates when you turn it close to its
end stop, so you know it's working.

## Everyday commands

| Command | What it does |
|---|---|
| `netxwheel status` | the wheels (with their numbers), where they are, which game uses them |
| `netxwheel watch` | the same, live (Ctrl+C to stop) |
| `netxwheel center 1` | hold wheel 1 straight first: saves that as straight ahead |
| `netxwheel reset 1` | wheel 1 back to its own centre |
| `netxwheel buzz 1 1.0` | vibrate wheel 1 for 2 seconds (to check the forces work) |
| `netxwheel kick 1` | end the game's session on wheel 1, so it is free again |
| `netxwheel name "Living room Pi"` | this server's name in the games' lists |
| `netxwheel password server SECRET` | a password for the whole server (`off`: none) |
| `netxwheel password wheel 2 SECRET` | a password for wheel 2 as well (`off`: none) |
| `netxwheel test` | pretend to be a game: lists servers and wheels, connects, shows what a game gets (`test ffb`: test forces) |
| `netxwheel settings` | all settings with explanations |
| `netxwheel set NAME VALUE` | change a setting, e.g. `netxwheel set gain 0.5` (applies at once) |
| `netxwheel restart` / `stop` / `start` | the server (Linux) |
| `netxwheel log` | the server's messages (Linux; `log live` keeps showing them) |
| `netxwheel help` | everything else |

On Windows, run them in a Command Prompt opened in the NetXWheel folder.

## Settings

`netxwheel settings` shows them all. The ones you'll want:

| Setting | Default | What it does |
|---|---|---|
| `gain` | 0.6 | overall force, 0.0 .. 1.0 |
| `name` | the computer's name | the server's name in games' lists |
| `steer_degrees` | 360 | how far you turn for the game's full lock (360 = 180 each way). GT3's original wheels turned about 200 |
| `invert_force` | no | `yes` if the wheel pulls the wrong way |
| `rumble_strength` | 0.3 | how strong a game's full vibration is (ABS, bumps) |
| `idle_spring` | 0.70 | centring force while no game is connected |
| `warn_start` | 0.83 | the end-stop warning vibration starts at this much of the wheel's turn |
| `idle_forces` | yes | `no`: the wheel stays limp while no game is connected |

They apply to all wheels and live in one file (`~/.config/netxwheel/settings.ini`, on Windows
`%APPDATA%\NetXWheel\settings.ini`) that updates never touch. Each wheel's own password and centre
are kept next to it in `wheels.ini`. Only you can read these files (they hold the passwords).

**About the passwords:** they never travel over the network, and every message during a session is
signed, so nobody else on the network can use your wheel, take over a session or fake its forces
or controls. The wheel's data is not encrypted (someone on the network could see where it points),
and NetXWheel is made for your local network, not the internet.

## Wheels

| Wheel | Linux / Pi | Windows | Notes |
|---|---|---|---|
| Logitech G920 | tested, everything works | not tested yet | profile `logitech-g920` |
| any other USB wheel | should work, generic | should work, generic | buttons in their own order until it has a profile |

**Your wheel isn't listed?** It most likely works already with the generic settings. To map its
buttons properly, run `netxwheel make-profile`: it asks you to turn the wheel and press each button,
then saves a profile. Please [share it](CONTRIBUTING.md) so it works for everyone.

Force feedback depends on the computer's driver for the wheel: Logitech wheels work on Linux out of
the box; some Thrustmaster and Fanatec models need an extra driver on Linux.

## For game developers

[PROTOCOL.md](PROTOCOL.md) is the whole protocol (version 1): UDP, small JSON messages to find
servers and connect, compact binary ones while driving.
Your game shows the network wheel as a normal wheel (for example an SDL virtual joystick) and sends
its forces back. [netxwheel/client.py](netxwheel/client.py) is a complete client in about 150 lines of
Python to read alongside the spec; `netxwheel test` shows everything from the game's side. A C++ client
(GT3 Revisited's) will be in [clients/](clients/).

## Troubleshooting

- **`netxwheel status` says no answer:** the server isn't running. `netxwheel start`, then
  `netxwheel log` to see why it stopped.
- **"no wheel found yet":** plug the wheel in, switch it on; `netxwheel devices` lists what the
  computer sees.
- **The game doesn't find the server:** both must be on the same network (some guest Wi-Fis block
  this). Type the server's address in the game; on the Pi, `hostname -I` shows it.
- **"in use":** another game has that wheel. It's free when that game leaves (or 3 seconds after it
  stops answering), or end it with `netxwheel kick N`.
- **"too many wrong passwords":** wait a minute, then try again with the right one.
- **Forgot a password:** set a new one on the server: `netxwheel password server NEW` (or `off`).
- **The wheel pulls the wrong way:** `netxwheel set invert_force yes`.
- **Too strong / too weak:** `netxwheel set gain 0.4` (or higher).
- **Steering too slow / too twitchy:** `netxwheel set steer_degrees 270` (quicker) or `540` (slower).

## Licence

MIT, see [LICENSE](LICENSE). Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
