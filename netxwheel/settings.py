"""Your settings: one small text file the server reads (and re-reads when it changes).

Linux:   ~/.config/netxwheel/settings.ini
Windows: %APPDATA%\\NetXWheel\\settings.ini

Change a setting with:  netxwheel set gain 0.5   (no editor needed; the server picks it up at once)
Updating NetXWheel never touches this file.
"""
import os
import re
import sys

# name, default, comment (in the order they appear in the file)
DEFAULTS = [
    ("port", "27920", "UDP port the game connects to"),
    ("gain", "0.6", "overall force on the wheel, 0.0 .. 1.0"),
    ("invert_force", "no", "yes if the steering force pushes the wrong way"),
    ("profile", "auto", "wheel profile: auto, or a profile's file name (e.g. logitech-g920)"),
    ("wheel_rotation", "auto", "the wheel's own rotation lock-to-lock in degrees; auto: from its profile"),
    ("steer_degrees", "360", "how far you turn for the game's full lock, lock-to-lock (360 = 180 each way)"),
    ("game_vibration", "0.27", "vibration strength in races (ABS, bumps), 0.0 .. 1.0"),
    ("idle_forces", "yes", "while no game is connected: centring force and the warning vibration"),
    ("idle_spring", "0.70", "centring force while no game is connected, 0.0 .. 1.0"),
    ("warn_start", "0.83", "warning vibration from this much of the wheel's own rotation (0.83 = 83%)"),
    ("warn_soft", "0.25", "warning vibration strength where it starts, 0.0 .. 1.0"),
    ("warn_strong", "0.60", "warning vibration strength at 99%, 0.0 .. 1.0"),
    ("warn_stop", "0.995", "no vibration from here (the end stop), so it doesn't buzz the mechanism"),
    ("center", "0.0", "straight-ahead offset, set by: netxwheel center (reset: netxwheel reset)"),
]
NAMES = [d[0] for d in DEFAULTS]
LINE = re.compile(r"^\s*([a-z_]+)\s*=\s*([^#]*?)\s*(#.*)?$")


def config_dir():
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NetXWheel")
    return os.path.join(os.path.expanduser("~"), ".config", "netxwheel")


def path():
    return os.path.join(config_dir(), "settings.ini")


def default_text():
    out = ["# NetXWheel settings. Change one with: netxwheel set NAME VALUE (e.g. netxwheel set gain 0.5)",
           "# Values: numbers, yes/no, or words as noted. The server picks up changes at once.", ""]
    for name, value, comment in DEFAULTS:
        out.append("%-15s = %-8s # %s" % (name, value, comment))
    return "\n".join(out) + "\n"


def ensure():
    """Creates the settings file with the defaults if it is missing; returns its path."""
    p = path()
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(default_text())
    return p


def read():
    """All settings as strings (defaults for any missing)."""
    values = {name: value for name, value, _ in DEFAULTS}
    try:
        with open(path()) as f:
            for line in f:
                m = LINE.match(line)
                if m and m.group(1) in values:
                    values[m.group(1)] = m.group(2)
    except OSError:
        pass
    return values


def mtime():
    try:
        return os.path.getmtime(path())
    except OSError:
        return 0.0


def check(name, value):
    """Returns the value cleaned up, or raises ValueError with a message for the user."""
    if name not in NAMES:
        raise ValueError("no setting called %r. Settings: %s" % (name, ", ".join(NAMES)))
    v = value.strip()
    if name in ("invert_force", "idle_forces"):
        if v.lower() not in ("yes", "no", "on", "off", "true", "false", "1", "0"):
            raise ValueError("%s must be yes or no" % name)
        return "yes" if v.lower() in ("yes", "on", "true", "1") else "no"
    if name == "profile":
        if not re.match(r"^[A-Za-z0-9_.-]+$", v):
            raise ValueError("profile must be auto or a profile name like logitech-g920")
        return v
    if name == "wheel_rotation" and v.lower() == "auto":
        return "auto"
    try:
        num = float(v)
    except ValueError:
        raise ValueError("%s must be a number" % name)
    limits = {"port": (1, 65535), "wheel_rotation": (90, 2520), "steer_degrees": (30, 2520),
              "center": (-0.5, 0.5)}
    lo, hi = limits.get(name, (0.0, 1.0))
    if not lo <= num <= hi:
        raise ValueError("%s must be between %g and %g" % (name, lo, hi))
    return str(int(num)) if name in ("port", "wheel_rotation", "steer_degrees") else v


def write(name, value):
    """Sets one setting in the file, keeping everything else (comments included)."""
    value = check(name, value)
    p = ensure()
    with open(p) as f:
        lines = f.read().splitlines()
    comment = dict((n, c) for n, _, c in DEFAULTS)[name]
    new = "%-15s = %-8s # %s" % (name, value, comment)
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m and m.group(1) == name:
            lines[i] = new
            break
    else:
        lines.append(new)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, p)
    return value


def yes(v):
    return str(v).strip().lower() in ("yes", "on", "true", "1")


class Settings:
    """The settings as numbers, for the server."""

    def __init__(self, values=None):
        v = values or read()
        f = lambda n, d: _num(v.get(n), d)
        self.port = int(f("port", 27920))
        self.gain = f("gain", 0.6)
        self.invert_force = yes(v.get("invert_force", "no"))
        self.profile = v.get("profile", "auto").strip() or "auto"
        rot = v.get("wheel_rotation", "auto").strip().lower()
        self.wheel_rotation = None if rot == "auto" else int(_num(rot, 900))
        self.steer_degrees = int(f("steer_degrees", 360))
        self.game_vibration = f("game_vibration", 0.27)
        self.idle_forces = yes(v.get("idle_forces", "yes"))
        self.idle_spring = f("idle_spring", 0.70)
        self.warn_start = f("warn_start", 0.83)
        self.warn_soft = f("warn_soft", 0.25)
        self.warn_strong = f("warn_strong", 0.60)
        self.warn_stop = f("warn_stop", 0.995)
        self.center = f("center", 0.0)


def _num(s, default):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default
