"""Linux backend: reads the wheel and plays forces through evdev (python3-evdev)."""
import errno

from evdev import InputDevice, ecodes, ff, list_devices

from . import protocol as P
from .profiles import AXIS_ROLES, ALL_BUTTON_ROLES, choose
from .state import WheelState, axis_centered, pedal

NAME = "Linux evdev"


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


def open_wheel(cfg, all_profiles):
    """The wheel to use and its profile: one matching a profile first, else anything wheel-like."""
    devices = []
    for path in list_devices():
        try:
            devices.append(InputDevice(path))
        except OSError:
            pass
    chosen = profile = None
    wanted = cfg.profile if cfg.profile != "auto" else None
    for d in devices:                      # 1. a device a profile names
        p = choose(d.name, cfg.profile, all_profiles)
        if p.id == "generic" or "linux" not in p.sections:
            continue
        if p.matches(d.name) or (wanted == p.id and _looks_like_wheel(d, _caps(d))):
            chosen, profile = d, p
            break
    if chosen is None:                     # 2. anything that looks like a wheel
        for d in devices:
            if _looks_like_wheel(d, _caps(d)):
                chosen, profile = d, choose(d.name, "auto", [])
                break
    for d in devices:
        if d is not chosen:
            d.close()
    if chosen is None:
        return None, None
    return EvdevWheel(chosen, profile), profile


class EvdevWheel:
    name = NAME

    def __init__(self, dev, profile):
        self.dev = dev
        self.device_name = dev.name
        self.state = WheelState()
        caps = _caps(dev)
        m = profile.platform("linux")
        # axes
        self.axes = {}                                     # code -> (role, lo, hi, rest_high)
        rest = m.get("pedals_rest", "auto")
        absc = _abs_codes(caps)
        for role in AXIS_ROLES:
            c = code(m.get(role, ""), "axis")
            if c is None or c not in absc:
                continue
            info = dev.absinfo(c)
            lo, hi = info.min, info.max
            rest_high = rest == "high" or (rest == "auto" and role != "steering" and info.value > (lo + hi) / 2)
            self.axes[c] = (role, lo, hi, rest_high)
            self._axis(c, info.value)
        hat = (m.get("hat") or "ABS_HAT0X ABS_HAT0Y").split()
        self.hat_x = code(hat[0], "axis") if hat else None
        self.hat_y = code(hat[1], "axis") if len(hat) > 1 else None
        # buttons
        self.keys = {}                                     # code -> role
        if m.get("buttons") == "in_order":
            roles = P.IN_ORDER_ROLES
            for i, c in enumerate(sorted(caps.get(ecodes.EV_KEY, []))[:len(P.IN_ORDER_ROLES)]):
                self.keys[c] = roles[i]
        for role in ALL_BUTTON_ROLES:
            if role in m:
                c = code(m[role], "button")
                if c is not None:
                    self.keys[c] = role
        self.has_shifter = any(r in P.GEAR_ROLES for r in self.keys.values())
        ffc = caps.get(ecodes.EV_FF, [])
        self.has_ffb = ecodes.FF_CONSTANT in ffc or ecodes.FF_SPRING in ffc
        self.supported = {"spring": ecodes.FF_SPRING in ffc, "const": ecodes.FF_CONSTANT in ffc,
                          "vib": ecodes.FF_PERIODIC in ffc}
        self.ids = {"spring": None, "const": None, "vib": None}
        self.last = {"spring": None, "const": None, "vib": None}
        if self.has_ffb:
            try:
                dev.write(ecodes.EV_FF, ecodes.FF_AUTOCENTER, 0)
            except OSError:
                pass

    # ------------------------------------------------------------ input
    def fileno(self):
        return self.dev.fd

    def _axis(self, c, value):
        role, lo, hi, rest_high = self.axes[c]
        if role == "steering":
            self.state.steer = axis_centered(value, lo, hi)
        else:
            setattr(self.state, role, pedal(value, lo, hi, rest_high))

    def poll(self):
        try:
            events = list(self.dev.read())
        except BlockingIOError:
            return
        for e in events:
            if e.type == ecodes.EV_ABS:
                if e.code in self.axes:
                    self._axis(e.code, e.value)
                elif e.code == self.hat_x:
                    self.state.hat_x = max(-1, min(1, e.value))
                elif e.code == self.hat_y:
                    self.state.hat_y = max(-1, min(1, e.value))
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
            print("this wheel refused the %s effect (%s); playing without it" % (name, e), flush=True)
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
        """Send every force again at the next update (the centre or settings changed)."""
        for name in self.last:
            self.last[name] = None

    def set_forces(self, spring, center, push, vib):
        """spring 0..1, center -1..1 (spring's centre), push -1..1 (positive turns right), vib 0..1."""
        if not self.has_ffb:
            return
        coef = int(spring * 32767)
        center_ff = int(max(-1.0, min(1.0, center)) * 32767)
        # The kernel's direction 0x4000 pushes left for a positive level, so the sign is flipped.
        level = int(-push * 32767)
        mag = int(vib * 32767)

        key = (coef, center_ff)
        if key != self.last["spring"]:
            if coef > 0:
                c = ff.Condition(0x7FFF, 0x7FFF, coef, coef, 0, center_ff)
                self._play("spring", ff.Effect(
                    ecodes.FF_SPRING, -1, 0, ff.Trigger(0, 0), ff.Replay(0, 0),
                    ff.EffectType(ff_condition_effect=(c, c))))
            else:
                self._stop_one("spring")
            self.last["spring"] = key

        if level != self.last["const"]:
            if level != 0:
                self._play("const", ff.Effect(
                    ecodes.FF_CONSTANT, -1, 0x4000, ff.Trigger(0, 0), ff.Replay(0, 0),
                    ff.EffectType(ff_constant_effect=ff.Constant(level, ff.Envelope(0, 0, 0, 0)))))
            else:
                self._stop_one("const")
            self.last["const"] = level

        if mag != self.last["vib"]:
            if mag > 300:
                self._play("vib", ff.Effect(
                    ecodes.FF_PERIODIC, -1, 0x4000, ff.Trigger(0, 0), ff.Replay(0, 0),
                    ff.EffectType(ff_periodic_effect=ff.Periodic(
                        ecodes.FF_SINE, 40, mag, 0, 0, ff.Envelope(0, 0, 0, 0), 0, None))))
            else:
                self._stop_one("vib")
            self.last["vib"] = mag

    def close(self):
        try:
            self.stop_forces()
        finally:
            self.dev.close()


def list_wheels():
    """(path, name, looks like a wheel) for every input device: for netxwheel make-profile."""
    out = []
    for path in list_devices():
        try:
            d = InputDevice(path)
        except OSError:
            continue
        out.append((path, d.name, _looks_like_wheel(d, _caps(d))))
        d.close()
    return out
