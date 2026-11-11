# Contributing to NetXWheel

Thanks for helping! The most useful things, easiest first:

## 1. Tell us how your wheel works

Open an issue with the **Wheel report** template: which wheel, which computer (Pi, Linux, Windows),
what works (steering, pedals, buttons, shifter, force feedback). That fills the table in the README.

## 2. Add a profile for your wheel (no coding)

1. Install NetXWheel and plug the wheel in.
2. Run `netxwheel make-profile` and follow it (turn the wheel, press the pedals and buttons).
3. It saves a file in your profiles folder (it tells you where) and the server uses it after
   `netxwheel restart`. Drive a bit to check it.
4. Share it: open an issue with the **New wheel profile** template and paste the file. Or, if you know
   GitHub, add it to `profiles/` in a pull request (name: `brand-model.ini`, e.g. `logitech-g29.ini`).

A profile is a short text file; `profiles/logitech-g920.ini` is an example. If one file can cover
Linux and Windows, add both a `[linux]` and a `[windows]` section (run make-profile on each).

## 3. Code

- The server must stay simple to install and run: Python 3 standard library plus `python3-evdev`
  (Linux) or `pysdl2` (Windows). No new dependencies without a good reason.
- Wheel-specific things go in profiles, not in code.
- The protocol: version 1 is being designed in pull request #1; comments welcome there.
- Run the tests before a pull request: `python3 tests/run_tests.py` (no wheel needed).
