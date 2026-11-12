"""netxwheel make-profile: teaches NetXWheel a new wheel by asking you to turn and press things.

It writes a profile file to your own profiles folder; the server uses it from its next start
(netxwheel restart). Please share it, so the wheel works for everyone (see CONTRIBUTING.md).
"""
import os
import re
import sys
import time

from . import profiles

LINUX = sys.platform.startswith("linux")

BUTTON_PROMPTS = [
    ("a", "the A button (or the button you'd use for 'OK')"),
    ("b", "the B button (or 'back')"),
    ("x", "the X button"),
    ("y", "the Y button"),
    ("paddle_right", "the RIGHT gear paddle (shift up)"),
    ("paddle_left", "the LEFT gear paddle (shift down)"),
    ("menu", "the menu / start / options button"),
    ("view", "the view / select / share button"),
    ("rsb", "the right stick / R3 button (if the wheel has one)"),
    ("lsb", "the left stick / L3 button (if the wheel has one)"),
    ("home", "the home / Xbox / PS / logo button"),
    ("l2", "the L2 button (if the wheel has one)"),
    ("r2", "the R2 button (if the wheel has one)"),
    ("plus", "the + button (if the wheel has one)"),
    ("minus", "the - button (if the wheel has one)"),
    ("enter", "the Enter / dial press button (if the wheel has one)"),
]
SHIFTER_PROMPTS = [("gear%d" % g, "put the shifter in gear %d" % g) for g in range(1, 8)] + [
    ("reverse", "put the shifter in REVERSE")]
DPAD_PROMPTS = [("dpad_up", "press the D-pad UP"), ("dpad_down", "press the D-pad DOWN"),
                ("dpad_left", "press the D-pad LEFT"), ("dpad_right", "press the D-pad RIGHT")]


# ---------------------------------------------------------------- keyboard: Enter skips
def enter_pressed():
    if LINUX or sys.platform == "darwin":
        import select
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            sys.stdin.readline()
            return True
        return False
    import msvcrt
    hit = False
    while msvcrt.kbhit():
        if msvcrt.getwch() in ("\r", "\n"):
            hit = True
    return hit


# ---------------------------------------------------------------- readers
class EvdevReader:
    kind = "linux"

    def __init__(self, path):
        from evdev import InputDevice, ecodes
        self.ec = ecodes
        self.dev = InputDevice(path)
        self.name = self.dev.name
        caps = self.dev.capabilities()
        self.ranges = {}
        self.hats = set()
        for c in caps.get(ecodes.EV_ABS, []):
            code, info = c if isinstance(c, tuple) else (c, self.dev.absinfo(c))
            if code in (ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y, ecodes.ABS_HAT1X, ecodes.ABS_HAT1Y):
                self.hats.add(code)
            else:
                self.ranges[code] = (info.min, info.max)

    def axis_name(self, code):
        n = self.ec.ABS.get(code, code)
        return n[0] if isinstance(n, list) else str(n)

    def read(self, timeout):
        """Events for up to timeout seconds: ('axis', id, 0..1) / ('button', id, down) / ('hat', id, v)."""
        import select
        out = []
        r, _, _ = select.select([self.dev.fd], [], [], timeout)
        if r:
            for e in self.dev.read():
                if e.type == self.ec.EV_ABS:
                    if e.code in self.ranges:
                        lo, hi = self.ranges[e.code]
                        out.append(("axis", e.code, (e.value - lo) / float(hi - lo or 1)))
                    elif e.code in self.hats and e.value != 0:
                        out.append(("hat", e.code, e.value))
                elif e.type == self.ec.EV_KEY and e.value == 1:
                    out.append(("button", e.code, True))
        return out

    def start_value(self, code):
        lo, hi = self.ranges[code]
        return (self.dev.absinfo(code).value - lo) / float(hi - lo or 1)

    def close(self):
        self.dev.close()


class SdlReader:
    kind = "windows"

    def __init__(self, index):
        import sdl2
        from . import backend_sdl
        backend_sdl._init()
        self.sdl = sdl2
        self.joy = sdl2.SDL_JoystickOpen(int(index))
        self.name = sdl2.SDL_JoystickName(self.joy).decode(errors="replace")
        self.naxes = sdl2.SDL_JoystickNumAxes(self.joy)
        self.nbuttons = sdl2.SDL_JoystickNumButtons(self.joy)
        self.nhats = sdl2.SDL_JoystickNumHats(self.joy)
        self.ranges = {i: (-32768, 32767) for i in range(self.naxes)}
        self.hats = set(range(self.nhats))
        sdl2.SDL_JoystickUpdate()
        self.buttons = [sdl2.SDL_JoystickGetButton(self.joy, i) for i in range(self.nbuttons)]
        self.hatv = [sdl2.SDL_JoystickGetHat(self.joy, i) for i in range(self.nhats)]

    def axis_name(self, i):
        return "axis%d" % i

    def read(self, timeout):
        s = self.sdl
        time.sleep(min(timeout, 0.05))
        s.SDL_JoystickUpdate()
        out = [("axis", i, (s.SDL_JoystickGetAxis(self.joy, i) + 32768) / 65535.0) for i in range(self.naxes)]
        for i in range(self.nbuttons):
            b = s.SDL_JoystickGetButton(self.joy, i)
            if b and not self.buttons[i]:
                out.append(("button", i, True))
            self.buttons[i] = b
        for i in range(self.nhats):
            h = s.SDL_JoystickGetHat(self.joy, i)
            if h and h != self.hatv[i]:
                out.append(("hat", i, h))
            self.hatv[i] = h
        return out

    def start_value(self, i):
        self.sdl.SDL_JoystickUpdate()
        return (self.sdl.SDL_JoystickGetAxis(self.joy, i) + 32768) / 65535.0

    def close(self):
        self.sdl.SDL_JoystickClose(self.joy)


# ---------------------------------------------------------------- steps
def countdown_axes(reader, seconds, used):
    """Watches every axis for some seconds; returns the one that moved most, its span and start."""
    lo, hi = {}, {}
    start = {c: reader.start_value(c) for c in reader.ranges}
    end = time.time() + seconds
    shown = None
    while time.time() < end:
        left = int(end - time.time()) + 1
        if left != shown:
            print("  %d..." % left, end="", flush=True)
            shown = left
        for kind, c, v in reader.read(0.05):
            if kind == "axis" and c not in used:
                lo[c] = min(lo.get(c, v), v, start[c])
                hi[c] = max(hi.get(c, v), v, start[c])
    print()
    if not lo:
        return None, 0.0, 0.0
    best = max(lo, key=lambda c: hi[c] - lo[c])
    return best, hi[best] - lo[best], start[best]


def wait_button(reader, seconds=8):
    """The first button pressed (or hat moved) within some seconds; None if skipped or none."""
    end = time.time() + seconds
    while time.time() < end:
        if enter_pressed():
            return None
        for kind, c, v in reader.read(0.05):
            if kind in ("button", "hat"):
                return (kind, c, v)
    return None


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "my-wheel"


def pick_device():
    if LINUX:
        from . import backend_evdev as mod
    else:
        from . import backend_sdl as mod
    rows = mod.list_wheels()
    if not rows:
        print("No controllers found. Plug the wheel in, switch it on, and try again.")
        return None
    print("Controllers:")
    default = 0
    for i, (path, name, wheel) in enumerate(rows):
        print("  %d. %s%s" % (i + 1, name, "   <- looks like a wheel" if wheel else ""))
        if wheel and not default:
            default = i + 1
    default = default or 1
    choice = input("Which one is your wheel? [%d] " % default).strip()
    try:
        idx = int(choice) - 1 if choice else default - 1
        path = rows[idx][0]
    except (ValueError, IndexError):
        print("That's not one of the numbers.")
        return None
    return EvdevReader(path) if LINUX else SdlReader(path)


def main():
    print(__doc__)
    reader = pick_device()
    if reader is None:
        return 1
    m = {}
    axis_name = reader.axis_name
    print("\nWheel: %s" % reader.name)
    print("\n1. STEERING: when the countdown starts, turn the wheel all the way left, then all the way")
    print("   right, then back to the middle.")
    input("   Press Enter to start... ")
    code, span, _ = countdown_axes(reader, 7, set())
    if code is None or span < 0.3:
        print("Didn't see the wheel turn. Is it the right controller? Try again.")
        return 1
    m["steering"] = axis_name(code)
    used = {code}
    print("   steering: %s" % m["steering"])

    rest_high = None
    for step, role, what in ((2, "throttle", "the THROTTLE (gas) pedal"), (3, "brake", "the BRAKE pedal"),
                             (4, "clutch", "the CLUTCH pedal (Enter, then leave it, if there's none)"),
                             (5, "handbrake", "the HANDBRAKE (Enter, then leave it, if there's none)")):
        print("\n%d. Press %s all the way down and let go, a couple of times." % (step, what))
        input("   Press Enter to start... ")
        code, span, start = countdown_axes(reader, 5, used)
        if code is None or span < 0.3:
            print("   no %s found - skipped" % role)
            continue
        m[role] = axis_name(code)
        used.add(code)
        if rest_high is None:
            rest_high = start > 0.5
        print("   %s: %s" % (role, m[role]))
    m["pedals_rest"] = "high" if rest_high else "low"

    print("\n6. BUTTONS: press each one when asked (press Enter to skip one the wheel doesn't have).")
    for role, prompt in BUTTON_PROMPTS:
        print("   %s ... " % prompt, end="", flush=True)
        got = wait_button(reader)
        if got and got[0] == "button":
            m[role] = ("button%d" % got[1]) if reader.kind == "windows" else str(got[1])
            print(m[role])
        else:
            print("skipped")

    print("\n7. D-PAD:")
    for role, prompt in DPAD_PROMPTS:
        print("   %s ... " % prompt, end="", flush=True)
        got = wait_button(reader)
        if got and got[0] == "hat":
            if reader.kind == "linux":
                m["hat"] = "ABS_HAT0X ABS_HAT0Y" if got[1] in (reader.ec.ABS_HAT0X, reader.ec.ABS_HAT0Y) \
                    else "ABS_HAT1X ABS_HAT1Y"
            else:
                m["hat"] = "hat%d" % got[1]
            print("a hat (all four directions found)")
            break
        if got and got[0] == "button":
            m[role] = ("button%d" % got[1]) if reader.kind == "windows" else str(got[1])
            print(m[role])
        else:
            print("skipped")

    if input("\n8. Do you have an H-shifter (gear stick with gears and R)? Gear 7 can be skipped. [y/N] ").strip().lower().startswith("y"):
        for role, prompt in SHIFTER_PROMPTS:
            print("   %s ... " % prompt, end="", flush=True)
            got = wait_button(reader)
            if got and got[0] == "button":
                m[role] = ("button%d" % got[1]) if reader.kind == "windows" else str(got[1])
                print(m[role])
            else:
                print("skipped")

    rot = input("\n9. How many degrees does the wheel turn from full left to full right? [900] ").strip()
    try:
        rotation = int(float(rot)) if rot else 900
    except ValueError:
        rotation = 900
    reader.close()

    vendor = input("\n10. The wheel's make, e.g. Logitech: ").strip()
    model = input("   and model, e.g. G29: ").strip()
    dd = input("   Is it a direct drive wheel? [y/N] ").strip().lower().startswith("y")
    torque = input("   Its strongest force in Nm, if you know it (Enter to skip): ").strip()
    try:
        torque = "%g" % float(torque.replace(",", ".")) if torque else ""
    except ValueError:
        torque = ""
    name = reader.name
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w and w.lower() not in (
        "logitech", "thrustmaster", "fanatec", "racing", "wheel", "driving", "force", "inc", "usb")]
    match = " ".join(words[:2]) or name
    os.makedirs(profiles.user_dir(), exist_ok=True)
    file = os.path.join(profiles.user_dir(), slug(name) + ".ini")
    lines = ["; Made with netxwheel make-profile.", "", "[wheel]", "name = %s" % name]
    if vendor:
        lines.append("vendor = %s" % vendor)
    if model:
        lines.append("model = %s" % model)
    lines += ["match = %s" % match, "rotation = %d" % rotation, "direct_drive = %s" % ("yes" if dd else "no")]
    if torque:
        lines.append("torque_nm = %s" % torque)
    lines += ["tested = no", "", "[%s]" % reader.kind]
    order = ["steering", "throttle", "brake", "clutch", "handbrake", "pedals_rest", "hat"] + [
        r for r, _ in BUTTON_PROMPTS + DPAD_PROMPTS + SHIFTER_PROMPTS]
    for role in order:
        if role in m:
            lines.append("%s = %s" % (role, m[role]))
    with open(file, "w") as f:
        f.write("\n".join(lines) + "\n")
    problems = profiles.validate(file)
    print("\nSaved: %s" % file)
    for p in problems:
        print("  note:", p)
    print("The server uses it from its next start: netxwheel restart")
    print("Please share it so the wheel works for everyone: https://github.com/IZImaster/NetXWheel/issues")
    print("(choose 'New wheel profile' and paste the file).")
    return 0
