"""Linux backend: reads wheels and plays forces through evdev (python3-evdev)."""
import errno

from evdev import InputDevice, ecodes, ff, list_devices

from . import protocol as P
from . import settings as S
from .profiles import AXIS_ROLES, ALL_BUTTON_ROLES, EXTRA_AXES, choose
from .state import WheelState, axis_centered, axis_unit, pedal

NAME = "Linux evdev"
KIND = "linux"


def code(value, kind):
    """An evdev code from a profile value: a number (288) or a name (ABS_X, BTN_TRIGGER)."""
    v = str(value).strip()
    if not v:
        return None
    if v.lstrip("-").isdigit():
        return int(v)
    c = ecodes.ecodes.get(v.upper())
    if c is None:
        raise ValueError("unknown %s name %r" % (kind, v))
    return c


def _caps(dev):
    try:
        return dev.capabilities()
    except OSError:
        return {}


def _abs_codes(caps):
    return [c[0] if isinstance(c, tuple) else c for c in caps.get(ecodes.EV_ABS, [])]


def _looks_like_wheel(dev, caps):
    absc = _abs_codes(caps)
    if ecodes.ABS_X not in absc:
        return False
    keys = caps.get(ecodes.EV_KEY, [])
    if ecodes.BTN_TOUCH in keys or ecodes.ABS_MT_POSITION_X in absc:
        return False                       # a touchpad or touchscreen
    name = dev.name.lower()
    ffc = caps.get(ecodes.EV_FF, [])
    has_force = ecodes.FF_CONSTANT in ffc or ecodes.FF_SPRING in ffc
    return has_force or any(w in name for w in ("wheel", "racing", "steering", "driving"))


def uid_of(dev):
    """Identifies a wheel across unplugging: its name and where it is plugged in (or its serial)."""
    return "%s@%s" % (dev.name, dev.uniq or dev.phys or dev.path)


def open_new(cfg, all_profiles, open_uids):
    """Every wheel not open yet: a list of wheels (each with .uid and .profile)."""
    found = []
    for path in list_devices():
        try:
            d = InputDevice(path)
        except OSError:
            continue
        uid = uid_of(d)
        if uid in open_uids or any(w.uid == uid for w in found):
            d.close()
            continue
        wanted = S.wheel(uid).get("profile") or "auto"
        p = choose(d.name, wanted, all_profiles)
        is_wheel = (p.id != "generic" and "linux" in p.sections and (p.matches(d.name) or wanted == p.id)) \
            or _looks_like_wheel(d, _caps(d))
        if not is_wheel:
            d.close()
            continue
        if "linux" not in p.sections:
            p = choose(d.name, "auto", [])           # generic
        try:
            found.append(EvdevWheel(d, p, uid))
        except (OSError, ValueError) as e:
            print("could not use %s: %s" % (d.name, e), flush=True)
            d.close()
    return found


class EvdevWheel:
    name = NAME

    def __init__(self, dev, profile, uid):
        self.dev = dev
        self.uid = uid
        self.profile = profile
        self.device_name = dev.name
        caps = _caps(dev)
        m = profile.platform("linux")
        self.labels = {k[6:]: v for k, v in m.items() if k.startswith("label_")}
        # axes
        self.axes = {}                                     # code -> (role, lo, hi, rest_high)
        rest = m.get("pedals_rest", "auto")
        absc = _abs_codes(caps)
        self.extra_labels = []
        for role in AXIS_ROLES + EXTRA_AXES:
            c = code(m.get(role, ""), "axis")
            if c is None or c not in absc:
                continue
            info = dev.absinfo(c)
            lo, hi = info.min, info.max
            pedal_like = role in AXIS_ROLES and role != "steering"
            rest_high = pedal_like and (rest == "high" or (rest == "auto" and info.value > (lo + hi) / 2))
            if role in EXTRA_AXES:
                self.extra_labels.append(m.get(role + "_label", "Axis %s" % role[len("axis_extra"):]))
                role = ("extra", len(self.extra_labels) - 1)
            self.axes[c] = (role, lo, hi, rest_high)
        self.state = WheelState(len(self.extra_labels))
        for c in self.axes:
            self._axis(c, dev.absinfo(c).value)
        hat = (m.get("hat") or "ABS_HAT0X ABS_HAT0Y").split()
        self.hat_x = code(hat[0], "axis") if hat else None
        self.hat_y = code(hat[1], "axis") if len(hat) > 1 else None
        self.hat_v = [0, 0]
        self.has_hat = self.hat_x in absc
        # buttons
        self.keys = {}                                     # code -> role
        if m.get("buttons") == "in_order":
            for i, c in enumerate(sorted(caps.get(ecodes.EV_KEY, []))[:len(P.IN_ORDER)]):
                self.keys[c] = P.IN_ORDER[i]
        for role in ALL_BUTTON_ROLES:
            if role in m:
                c = code(m[role], "button")
                if c is not None:
                    self.keys[c] = role
        # force feedback
        ffc = caps.get(ecodes.EV_FF, [])
        self.supported = {"spring": ecodes.FF_SPRING in ffc, "const": ecodes.FF_CONSTANT in ffc,
                          "damper": ecodes.FF_DAMPER in ffc, "friction": ecodes.FF_FRICTION in ffc,
                          "vib": ecodes.FF_PERIODIC in ffc}
        self.has_ffb = self.supported["const"] or self.supported["spring"]
        self.ids = {k: None for k in self.supported}
        self.last = {k: None for k in self.supported}
        if self.has_ffb:
            try:
                dev.write(ecodes.EV_FF, ecodes.FF_AUTOCENTER, 0)
            except OSError:
                pass

    # ------------------------------------------------------------ what it has
    def describe(self):
        roles = set(self.keys.values())
        axes = [a[0] for a in self.axes.values()]
        gears = [r for r in roles if r in P.GEARS and r != "reverse"]
        if "shift_up" in roles and "shift_down" in roles and not gears:
            shifter = "sequential"
        elif gears:
            shifter = "h%dr" % max(int(g[4:]) for g in gears) if "reverse" in roles else "h%d" % len(gears)
        else:
            shifter = "none"
        forces = [n for n, k in (("constant", "const"), ("spring", "spring"), ("damper", "damper"),
                                 ("friction", "friction"), ("rumble", "vib")) if self.supported[k]]
        buttons = sorted(({"bit": P.BUTTONS[r][0], "role": r, "label": self.labels.get(r, P.BUTTONS[r][1])}
                          for r in roles if r in P.BUTTONS), key=lambda b: b["bit"])
        return {"pedals": [r for r in ("throttle", "brake", "clutch", "handbrake") if r in axes],
                "shifter": shifter, "forces": forces, "ffb": self.has_ffb, "buttons": buttons,
                "dpad": self.has_hat or any(r in P.DPAD for r in roles),
                "extra_axes": [{"role": "axis_extra%d" % (i + 1), "label": lab}
                               for i, lab in enumerate(self.extra_labels)]}

    # ------------------------------------------------------------ input
    def fileno(self):
        return self.dev.fd

    def _axis(self, c, value):
        role, lo, hi, rest_high = self.axes[c]
        st = self.state
        if role == "steering":
            st.steer = axis_centered(value, lo, hi)
        elif isinstance(role, tuple):
            st.extra[role[1]] = axis_unit(value, lo, hi)
        else:
            setattr(st, role, pedal(value, lo, hi, rest_high))

    def poll(self):
        try:
            events = list(self.dev.read())
        except BlockingIOError:
            return
        for e in events:
            if e.type == ecodes.EV_ABS:
                if e.code in self.axes:
                    self._axis(e.code, e.value)
                elif e.code == self.hat_x or e.code == self.hat_y:
                    self.hat_v[0 if e.code == self.hat_x else 1] = max(-1, min(1, e.value))
                    self.state.set_hat(*self.hat_v)
            elif e.type == ecodes.EV_KEY and e.code in self.keys:
                self.state.set_role(self.keys[e.code], e.value != 0)

    # ------------------------------------------------------------ force feedback
    def set_gain(self, gain):
        if self.has_ffb:
            self.dev.write(ecodes.EV_FF, ecodes.FF_GAIN, int(max(0.0, min(1.0, gain)) * 0xFFFF))

    def _play(self, name, effect):
        if not self.supported[name]:
            return
        effect.id = self.ids[name] if self.ids[name] is not None else -1
        try:
            eid = self.dev.upload_effect(effect)
        except OSError as e:
            if e.errno in (errno.ENODEV, errno.EIO):
                raise
            print("%s refused the %s effect (%s); playing without it" % (self.device_name, name, e), flush=True)
            self.supported[name] = False
            return
        if self.ids[name] is None:
            self.ids[name] = eid
            self.dev.write(ecodes.EV_FF, eid, 1)

    def _stop_one(self, name):
        eid = self.ids[name]
        if eid is not None:
            try:
                self.dev.write(ecodes.EV_FF, eid, 0)
                self.dev.erase_effect(eid)
            except OSError:
                pass
        self.ids[name] = None
        self.last[name] = None

    def stop_forces(self):
        for name in self.ids:
            self._stop_one(name)

    def refresh_forces(self):
        for name in self.last:
            self.last[name] = None

    def _condition(self, name, etype, coef, center=0):
        key = (coef, center)
        if key == self.last[name]:
            return
        if coef > 0:
            c = ff.Condition(0x7FFF, 0x7FFF, coef, coef, 0, center)
            self._play(name, ff.Effect(etype, -1, 0, ff.Trigger(0, 0), ff.Replay(0, 0),
                                       ff.EffectType(ff_condition_effect=(c, c))))
        else:
            self._stop_one(name)
        self.last[name] = key

    def set_forces(self, constant, spring, center, damper, friction, rumble, period_ms):
        """constant -1..1 (positive turns right), spring/damper/friction/rumble 0..1,
        center -1..1 of the wheel's own rotation, period_ms of the vibration."""
        if not self.has_ffb:
            return
        self._condition("spring", ecodes.FF_SPRING, int(spring * 32767), int(max(-1.0, min(1.0, center)) * 32767))
        self._condition("damper", ecodes.FF_DAMPER, int(damper * 32767))
        self._condition("friction", ecodes.FF_FRICTION, int(friction * 32767))
        # The kernel's direction 0x4000 pushes left for a positive level, so the sign is flipped.
        level = int(-constant * 32767)
        if level != self.last["const"]:
            if level != 0:
                self._play("const", ff.Effect(
                    ecodes.FF_CONSTANT, -1, 0x4000, ff.Trigger(0, 0), ff.Replay(0, 0),
                    ff.EffectType(ff_constant_effect=ff.Constant(level, ff.Envelope(0, 0, 0, 0)))))
            else:
                self._stop_one("const")
            self.last["const"] = level
        mag = int(rumble * 32767)
        key = (mag, int(period_ms))
        if key != self.last["vib"]:
            if mag > 300:
                self._play("vib", ff.Effect(
                    ecodes.FF_PERIODIC, -1, 0x4000, ff.Trigger(0, 0), ff.Replay(0, 0),
                    ff.EffectType(ff_periodic_effect=ff.Periodic(
                        ecodes.FF_SINE, int(period_ms), mag, 0, 0, ff.Envelope(0, 0, 0, 0), 0, None))))
            else:
                self._stop_one("vib")
            self.last["vib"] = key

    def close(self):
        try:
            self.stop_forces()
        finally:
            self.dev.close()


def list_wheels():
    """(path, name, looks like a wheel) for every input device: for netxwheel devices / make-profile."""
    out = []
    for path in list_devices():
        try:
            d = InputDevice(path)
        except OSError:
            continue
        out.append((path, d.name, _looks_like_wheel(d, _caps(d))))
        d.close()
    return out
