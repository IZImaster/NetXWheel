"""Your settings: small text files the server reads (and re-reads when they change).

Linux:   ~/.config/netxwheel/settings.ini   (and wheels.ini: each wheel's own password and centre)
Windows: %APPDATA%\\NetXWheel\\settings.ini

Change a setting with:  netxwheel set gain 0.5   (no editor needed; the server picks it up at once)
Updating NetXWheel never touches these files.
"""
import configparser
import os
import re
import socket
import sys

# name, default, comment (in the order they appear in the file)
DEFAULTS = [
    ("name", "", "this server's name, shown in games' lists (empty: the computer's name)"),
    ("server_password", "", "password for the whole server (empty: none). Set with: netxwheel password server"),
    ("port", "27920", "UDP port the games connect to"),
    ("gain", "0.6", "overall force on the wheels, 0.0 .. 1.0"),
    ("invert_force", "no", "yes if the steering force pushes the wrong way"),
    ("steer_degrees", "360", "how far you turn for the game's full lock, lock-to-lock (360 = 180 each way)"),
    ("rumble_strength", "0.3", "how strong a game's full vibration is on the wheel, 0.0 .. 1.0"),
    ("idle_forces", "yes", "while no game uses a wheel: centring force and the warning vibration"),
    ("idle_spring", "0.70", "centring force while no game uses the wheel, 0.0 .. 1.0"),
    ("warn_start", "0.83", "warning vibration from this much of the wheel's own rotation (0.83 = 83%)"),
    ("warn_soft", "0.25", "warning vibration strength where it starts, 0.0 .. 1.0"),
    ("warn_strong", "0.60", "warning vibration strength at 99%, 0.0 .. 1.0"),
    ("warn_stop", "0.995", "no vibration from here (the end stop), so it doesn't buzz the mechanism"),
]
NAMES = [d[0] for d in DEFAULTS]
SECRET = {"server_password"}
TEXT = {"name", "server_password"}
LINE = re.compile(r"^\s*([a-z_]+)\s*=\s*(.*?)\s*(#.*)?$")
QUOTED = re.compile(r'^"(.*)"$')


def config_dir():
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NetXWheel")
    return os.path.join(os.path.expanduser("~"), ".config", "netxwheel")


def path():
    return os.path.join(config_dir(), "settings.ini")


def _fmt(name, value, comment):
    v = '"%s"' % value if name in TEXT else value
    return "%-15s = %-8s # %s" % (name, v, comment)


def default_text():
    out = ["# NetXWheel settings. Change one with: netxwheel set NAME VALUE (e.g. netxwheel set gain 0.5)",
           "# Values: numbers, yes/no, or text in quotes. The server picks up changes at once.", ""]
    for name, value, comment in DEFAULTS:
        out.append(_fmt(name, value, comment))
    return "\n".join(out) + "\n"


def _private(p):
    try:
        os.chmod(p, 0o600)      # passwords live here: only you can read it
    except OSError:
        pass


def ensure():
    """Creates the settings file with the defaults if it is missing (adds new settings to an old one)."""
    p = path()
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(default_text())
    else:
        have = _read_file(p)
        missing = [d for d in DEFAULTS if d[0] not in have]
        if missing:
            with open(p, "a") as f:
                for name, value, comment in missing:
                    f.write(_fmt(name, value, comment) + "\n")
    _private(p)
    return p


def _read_file(p):
    values = {}
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                m = LINE.match(line)
                if m:
                    v = m.group(2)
                    q = QUOTED.match(v)
                    values[m.group(1)] = q.group(1) if q else v
    except OSError:
        pass
    return values


def read():
    """All settings as strings (defaults for any missing)."""
    values = {name: value for name, value, _ in DEFAULTS}
    for k, v in _read_file(path()).items():
        if k in values or k == "center":       # (center: from NetXWheel 0.1, moved to wheels.ini)
            values[k] = v
    return values


def mtime():
    t = 0.0
    for p in (path(), wheels_path()):
        try:
            t = max(t, os.path.getmtime(p))
        except OSError:
            pass
    return t


def check(name, value):
    """Returns the value cleaned up, or raises ValueError with a message for the user."""
    if name not in NAMES:
        raise ValueError("no setting called %r. Settings: %s" % (name, ", ".join(NAMES)))
    v = value.strip()
    if name in TEXT:
        if '"' in v or "\n" in v or len(v) > 64:
            raise ValueError("%s: at most 64 characters, no quotes" % name)
        return v
    if name in ("invert_force", "idle_forces"):
        if v.lower() not in ("yes", "no", "on", "off", "true", "false", "1", "0"):
            raise ValueError("%s must be yes or no" % name)
        return "yes" if v.lower() in ("yes", "on", "true", "1") else "no"
    try:
        num = float(v)
    except ValueError:
        raise ValueError("%s must be a number" % name)
    limits = {"port": (1, 65535), "steer_degrees": (30, 2520)}
    lo, hi = limits.get(name, (0.0, 1.0))
    if not lo <= num <= hi:
        raise ValueError("%s must be between %g and %g" % (name, lo, hi))
    return str(int(num)) if name in ("port", "steer_degrees") else v


def write(name, value):
    """Sets one setting in the file, keeping everything else (comments included)."""
    value = check(name, value)
    p = ensure()
    with open(p, encoding="utf-8") as f:
        lines = f.read().splitlines()
    comment = dict((n, c) for n, _, c in DEFAULTS)[name]
    new = _fmt(name, value, comment)
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m and m.group(1) == name:
            lines[i] = new
            break
    else:
        lines.append(new)
    _replace(p, "\n".join(lines) + "\n")
    return value


def _replace(p, text):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    _private(tmp)
    os.replace(tmp, p)


def yes(v):
    return str(v).strip().lower() in ("yes", "on", "true", "1")


def _num(s, default):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


class Settings:
    """The settings as numbers, for the server."""

    def __init__(self, values=None):
        v = values or read()
        f = lambda n, d: _num(v.get(n), d)
        self.name = v.get("name", "").strip() or socket.gethostname()
        self.server_password = v.get("server_password", "")
        self.port = int(f("port", 27920))
        self.gain = f("gain", 0.6)
        self.invert_force = yes(v.get("invert_force", "no"))
        self.steer_degrees = int(f("steer_degrees", 360))
        self.rumble_strength = f("rumble_strength", 0.3)
        self.idle_forces = yes(v.get("idle_forces", "yes"))
        self.idle_spring = f("idle_spring", 0.70)
        self.warn_start = f("warn_start", 0.83)
        self.warn_soft = f("warn_soft", 0.25)
        self.warn_strong = f("warn_strong", 0.60)
        self.warn_stop = f("warn_stop", 0.995)
        self.old_center = f("center", 0.0)        # NetXWheel 0.1's single-wheel centre


# ---------------------------------------------------------------- each wheel's own settings
def wheels_path():
    return os.path.join(config_dir(), "wheels.ini")


WHEEL_KEYS = {"password": "", "center": "0.0", "steer_degrees": "", "profile": ""}


def _wheels_parser():
    cp = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    cp.optionxform = str
    try:
        cp.read(wheels_path(), encoding="utf-8")
    except configparser.Error:
        pass
    return cp


def wheel(uid):
    """One wheel's own settings (by its uid): password, center, steer_degrees, profile."""
    cp = _wheels_parser()
    out = dict(WHEEL_KEYS)
    if cp.has_section(uid):
        for k in WHEEL_KEYS:
            if cp.has_option(uid, k):
                out[k] = cp.get(uid, k)
    return out


def write_wheel(uid, key, value, label=None):
    if key not in WHEEL_KEYS:
        raise ValueError("unknown wheel setting %r" % key)
    os.makedirs(config_dir(), exist_ok=True)
    cp = _wheels_parser()
    if not cp.has_section(uid):
        cp.add_section(uid)
    if label:
        cp.set(uid, "wheel", label)
    if value == "" and key != "password":
        cp.remove_option(uid, key)
    else:
        cp.set(uid, key, value)
    import io
    buf = io.StringIO()
    buf.write("# Each wheel's own settings, by the wheel's id. Change them with netxwheel commands\n"
              "# (netxwheel password wheel N, netxwheel center N), not by hand.\n\n")
    cp.write(buf)
    _replace(wheels_path(), buf.getvalue())
