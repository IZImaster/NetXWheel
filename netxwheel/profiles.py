"""Wheel profiles: one small text file per wheel model (profiles/*.ini), no code.

A profile says how to find the wheel and which of its axes and buttons are which:

    [wheel]
    name = Logitech G920 Driving Force
    match = G920              # words in the device's name; "|" separates alternatives
    rotation = 900            # the wheel's own rotation, lock-to-lock, in degrees

    [linux]                   # evdev names or numbers (netxwheel make-profile writes this for you)
    steering = ABS_X
    throttle = ABS_Y
    a = 288
    ...

    [windows]                 # SDL numbers: axis0, button3, hat0
    steering = axis0
    ...

Profiles come from the profiles folder that ships with NetXWheel and from your own
profiles folder (~/.config/netxwheel/profiles, or %APPDATA%\\NetXWheel\\profiles on Windows),
where netxwheel make-profile saves new ones. A wheel without a profile still works with the
generic settings below.
"""
import configparser
import glob
import os

from . import protocol, settings

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(HERE, "profiles")
AXIS_ROLES = ["steering", "throttle", "brake", "clutch"]
ALL_BUTTON_ROLES = list(protocol.BUTTON_BIT) + protocol.DPAD_ROLES

# Used for a wheel with no profile: steering on the first axis, pedals on the next ones,
# pedals' resting end found when the server starts, buttons in order.
GENERIC = {
    "wheel": {"name": "Generic wheel", "match": "", "rotation": "900", "tested": "no"},
    "linux": {"steering": "ABS_X", "throttle": "ABS_Y", "brake": "ABS_Z", "clutch": "ABS_RZ",
              "pedals_rest": "auto", "buttons": "in_order"},
    "windows": {"steering": "axis0", "throttle": "axis1", "brake": "axis2", "clutch": "axis3",
                "pedals_rest": "auto", "buttons": "in_order"},
}


class Profile:
    def __init__(self, sections, file=None):
        self.file = file
        self.id = os.path.splitext(os.path.basename(file))[0] if file else "generic"
        w = sections.get("wheel", {})
        self.name = w.get("name", self.id)
        self.match = [alt.split() for alt in w.get("match", "").lower().split("|") if alt.strip()]
        try:
            self.rotation = int(float(w.get("rotation", "900")))
        except ValueError:
            self.rotation = 900
        self.tested = w.get("tested", "no")
        self.sections = sections

    def platform(self, name):
        """The [linux] or [windows] mapping: role -> string (empty if the profile has none)."""
        return dict(self.sections.get(name, {}))

    def matches(self, device_name):
        n = (device_name or "").lower()
        return any(all(word in n for word in alt) for alt in self.match)

    def __repr__(self):
        return "Profile(%s)" % self.id


def _read(file):
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"), interpolation=None)
    cp.optionxform = str.lower
    with open(file, encoding="utf-8") as f:
        cp.read_file(f)
    return {s: dict(cp.items(s)) for s in cp.sections()}


def user_dir():
    return os.path.join(settings.config_dir(), "profiles")


def load_all():
    """All profiles: your own first (they win over shipped ones with the same file name)."""
    found = {}
    for folder in (user_dir(), SHIPPED):
        for file in sorted(glob.glob(os.path.join(folder, "*.ini"))):
            pid = os.path.splitext(os.path.basename(file))[0]
            if pid in found:
                continue
            try:
                found[pid] = Profile(_read(file), file)
            except (configparser.Error, OSError, UnicodeDecodeError) as e:
                print("skipping the profile %s: %s" % (file, e))
    return list(found.values())


def generic():
    return Profile(GENERIC)


def choose(device_name, wanted="auto", all_profiles=None):
    """The profile for a device: the one named in settings, else the first that matches, else generic."""
    profs = all_profiles if all_profiles is not None else load_all()
    if wanted and wanted != "auto":
        for p in profs:
            if p.id == wanted:
                return p
    for p in profs:
        if p.matches(device_name):
            return p
    return generic()


def validate(file):
    """Problems in a profile file, as a list of messages (empty: fine). Used by the tests."""
    problems = []
    try:
        s = _read(file)
    except Exception as e:  # noqa: BLE001 - any parse error is a problem to report
        return ["cannot be read: %s" % e]
    if "wheel" not in s:
        problems.append("has no [wheel] section")
    else:
        if not s["wheel"].get("name"):
            problems.append("[wheel] has no name")
        if not s["wheel"].get("match"):
            problems.append("[wheel] has no match (words in the wheel's device name)")
        try:
            r = int(float(s["wheel"].get("rotation", "900")))
            if not 90 <= r <= 2520:
                problems.append("[wheel] rotation should be between 90 and 2520")
        except ValueError:
            problems.append("[wheel] rotation is not a number")
    if "linux" not in s and "windows" not in s:
        problems.append("needs a [linux] or [windows] section")
    known = set(AXIS_ROLES) | set(ALL_BUTTON_ROLES) | {"pedals_rest", "buttons", "hat"}
    for plat in ("linux", "windows"):
        for key, value in s.get(plat, {}).items():
            if key not in known:
                problems.append("[%s] %s is not a known role" % (plat, key))
            if key == "pedals_rest" and value not in ("auto", "high", "low"):
                problems.append("[%s] pedals_rest must be auto, high or low" % plat)
    return problems
