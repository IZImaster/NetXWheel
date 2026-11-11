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

**Games that support it:** GT3 Revisited on Meta Quest and PC,
in [this fork](https://github.com/IZImaster/GT3-revisited-Steering-Wheel-FFB/tree/netwheel) for now.
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
2. In the game, switch the network wheel on. In GT3 Revisited:
   **Settings > Controls > Network wheel**. It finds the server by itself (or type its address).
3. Drive.

While no game is connected the wheel gently centres itself and vibrates when you turn it close
to its end stop, so you know it's working.

## Everyday commands

| Command | What it does |
|---|---|
| `netxwheel status` | where the wheel is, whether a game is connected |
| `netxwheel watch` | the same, live (Ctrl+C to stop) |
| `netxwheel center` | hold the wheel straight first: saves that as straight ahead |
| `netxwheel reset` | back to the wheel's own centre |
| `netxwheel buzz 1.0` | vibrate for 2 seconds (to check the forces work) |
| `netxwheel test` | pretend to be a game: shows exactly what a game gets (`test ffb`: test forces) |
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
| `steer_degrees` | 360 | how far you turn for the game's full lock (360 = 180 each way). GT3's original wheels turned about 200 |
| `invert_force` | no | `yes` if the wheel pulls the wrong way |
| `game_vibration` | 0.27 | vibration in races (ABS, bumps) |
| `idle_spring` | 0.70 | centring force while no game is connected |
| `warn_start` | 0.83 | the end-stop warning vibration starts at this much of the wheel's turn |
| `idle_forces` | yes | `no`: the wheel stays limp while no game is connected |

They live in one file (`~/.config/netxwheel/settings.ini`, on Windows
`%APPDATA%\NetXWheel\settings.ini`) that updates never touch.

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

[PROTOCOL.md](PROTOCOL.md) is the whole protocol: UDP, five small messages, frozen at version 1.
Your game shows the network wheel as a normal wheel (for example an SDL virtual joystick) and sends
its forces back. [clients/gt3-revisited](clients/gt3-revisited) is a complete client in C++ you can
copy; `netxwheel test` and [netxwheel/testclient.py](netxwheel/testclient.py) show it from the game's side.

## Troubleshooting

- **`netxwheel status` says no answer:** the server isn't running. `netxwheel start`, then
  `netxwheel log` to see why it stopped.
- **"no wheel found yet":** plug the wheel in, switch it on; `netxwheel devices` lists what the
  computer sees.
- **The game doesn't find the server:** both must be on the same network (some guest Wi-Fis block
  this). Type the server's address in the game; on the Pi, `hostname -I` shows it.
- **The wheel pulls the wrong way:** `netxwheel set invert_force yes`.
- **Too strong / too weak:** `netxwheel set gain 0.4` (or higher).
- **Steering too slow / too twitchy:** `netxwheel set steer_degrees 270` (quicker) or `540` (slower).

## Licence

MIT, see [LICENSE](LICENSE). Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
