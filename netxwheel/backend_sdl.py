"""Windows (and macOS/Linux, if asked) backend: wheels and their forces through SDL2 (pysdl2).

Install:  pip install pysdl2 pysdl2-dll
The effects are built like GT3 Revisited's own wheel code: on the steering axis, one infinite play.
"""
import ctypes

try:
    import sdl2
except ImportError as e:  # pragma: no cover - depends on the machine
    raise SystemExit("NetXWheel needs pysdl2 on this system: pip install pysdl2 pysdl2-dll (%s)" % e)

from . import protocol as P
from . import settings as S
from .profiles import AXIS_ROLES, ALL_BUTTON_ROLES, EXTRA_AXES, choose
from .state import WheelState, axis_centered, axis_unit, pedal

NAME = "SDL"
KIND = "windows"
_ready = False
_open_instances = set()


def _init():
    global _ready
    if _ready:
        return
    sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")
    if sdl2.SDL_Init(sdl2.SDL_INIT_JOYSTICK | sdl2.SDL_INIT_HAPTIC) != 0:
        raise SystemExit("SDL could not start: %s" % sdl2.SDL_GetError().decode(errors="replace"))
    sdl2.SDL_JoystickEventState(sdl2.SDL_IGNORE)   # read the wheels directly, no event queue
    _ready = True


def _num(value, prefix):
    v = str(value).strip().lower()
    if v.startswith(prefix):
        v = v[len(prefix):]
    return int(v) if v.isdigit() else None


def _name_for_index(i):
    n = sdl2.SDL_JoystickNameForIndex(i)
    return n.decode("utf-8", "replace") if n else "joystick %d" % i


def _looks_like_wheel(i, name):
    try:
        if sdl2.SDL_JoystickGetDeviceType(i) == sdl2.SDL_JOYSTICK_TYPE_WHEEL:
            return True
    except AttributeError:
        pass
    n = name.lower()
    return any(w in n for w in ("wheel", "racing", "steering", "driving", "g920", "g29", "g923"))


def _uid(joy, name, taken):
    serial = None
    try:
        s = sdl2.SDL_JoystickGetSerial(joy)
        serial = s.decode(errors="replace") if s else None
    except AttributeError:
        pass
    if serial:
        return "%s@%s" % (name, serial)
    base = "%s@sdl" % name
    uid, n = base, 1
    while uid in taken:            # two of the same model without serials: number them
        n += 1
        uid = "%s#%d" % (base, n)
    return uid


def open_new(cfg, all_profiles, open_uids):
    _init()
    sdl2.SDL_JoystickUpdate()
    found = []
    for i in range(sdl2.SDL_NumJoysticks()):
        inst = sdl2.SDL_JoystickGetDeviceInstanceID(i)
        if inst in _open_instances:
            continue
        name = _name_for_index(i)
        p_auto = choose(name, "auto", all_profiles)
        is_profiled = p_auto.id != "generic" and "windows" in p_auto.sections
        if not (is_profiled or _looks_like_wheel(i, name)):
            continue
        joy = sdl2.SDL_JoystickOpen(i)
        if not joy:
            continue
        uid = _uid(joy, name, set(open_uids) | {w.uid for w in found})
        wanted = S.wheel(uid).get("profile") or "auto"
        p = choose(name, wanted, all_profiles)
        if "windows" not in p.sections:
            p = choose(name, "auto", [])
        _open_instances.add(inst)
        found.append(SdlWheel(joy, p, uid, inst))
    return found


def _direction(e_dir):
    steering = getattr(sdl2, "SDL_HAPTIC_STEERING_AXIS", None)
    if steering is not None:
        e_dir.type = steering
    else:
        e_dir.type = sdl2.SDL_HAPTIC_CARTESIAN
        e_dir.dir[0] = 1


class SdlWheel:
    name = NAME

    def __init__(self, joy, profile, uid, inst):
        self.joy = joy
        self.uid = uid
        self.inst = inst
        self.profile = profile
        n = sdl2.SDL_JoystickName(joy)
        self.device_name = n.decode("utf-8", "replace") if n else "wheel"
        m = profile.platform("windows")
        self.labels = {k[6:]: v for k, v in m.items() if k.startswith("label_")}
        rest = m.get("pedals_rest", "auto")
        naxes = sdl2.SDL_JoystickNumAxes(joy)
        self.axes = []                                    # (index, role, rest_high)
        self.extra_labels = []
        for role in AXIS_ROLES + EXTRA_AXES:
            idx = _num(m.get(role, ""), "axis")
            if idx is None or idx >= naxes:
                continue
            value = sdl2.SDL_JoystickGetAxis(joy, idx)
            pedal_like = role in AXIS_ROLES and role != "steering"
            rest_high = pedal_like and (rest == "high" or (rest == "auto" and value > 0))
            if role in EXTRA_AXES:
                self.extra_labels.append(m.get(role + "_label", "Axis %s" % role[len("axis_extra"):]))
                role = ("extra", len(self.extra_labels) - 1)
            self.axes.append((idx, role, rest_high))
        self.state = WheelState(len(self.extra_labels))
        self.hat = _num(m.get("hat", "hat0"), "hat")
        if self.hat is not None and self.hat >= sdl2.SDL_JoystickNumHats(joy):
            self.hat = None
        nbuttons = sdl2.SDL_JoystickNumButtons(joy)
        self.buttons = {}                                 # index -> role
        if m.get("buttons") == "in_order":
            for i in range(min(nbuttons, len(P.IN_ORDER))):
                self.buttons[i] = P.IN_ORDER[i]
        for role in ALL_BUTTON_ROLES:
            if role in m:
                idx = _num(m[role], "button")
                if idx is not None and idx < nbuttons:
                    self.buttons[idx] = role
        self.pressed = {}
        # force feedback
        self.haptic = sdl2.SDL_HapticOpenFromJoystick(joy)
        q = sdl2.SDL_HapticQuery(self.haptic) if self.haptic else 0
        self.query = q
        self.supported = {"spring": bool(q & sdl2.SDL_HAPTIC_SPRING), "const": bool(q & sdl2.SDL_HAPTIC_CONSTANT),
                          "damper": bool(q & sdl2.SDL_HAPTIC_DAMPER), "friction": bool(q & sdl2.SDL_HAPTIC_FRICTION),
                          "vib": bool(q & sdl2.SDL_HAPTIC_SINE)}
        self.has_ffb = self.supported["spring"] or self.supported["const"]
        self.ids = {k: None for k in self.supported}
        self.last = {k: None for k in self.supported}
        if self.haptic and q & sdl2.SDL_HAPTIC_AUTOCENTER:
            sdl2.SDL_HapticSetAutocenter(self.haptic, 0)

    def describe(self):
        roles = set(self.buttons.values())
        axes = [a[1] for a in self.axes]
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
                "dpad": self.hat is not None or any(r in P.DPAD for r in roles),
                "extra_axes": [{"role": "axis_extra%d" % (i + 1), "label": lab}
                               for i, lab in enumerate(self.extra_labels)]}

    def fileno(self):
        return None        # polled

    def poll(self):
        sdl2.SDL_JoystickUpdate()
        if not sdl2.SDL_JoystickGetAttached(self.joy):
            raise OSError("the wheel was unplugged")
        st = self.state
        for idx, role, rest_high in self.axes:
            v = sdl2.SDL_JoystickGetAxis(self.joy, idx)
            if role == "steering":
                st.steer = axis_centered(v, -32768, 32767)
            elif isinstance(role, tuple):
                st.extra[role[1]] = axis_unit(v, -32768, 32767)
            else:
                setattr(st, role, pedal(v, -32768, 32767, rest_high))
        for idx, role in self.buttons.items():
            down = bool(sdl2.SDL_JoystickGetButton(self.joy, idx))
            if self.pressed.get(idx) != down:
                self.pressed[idx] = down
                st.set_role(role, down)
        if self.hat is not None:
            h = sdl2.SDL_JoystickGetHat(self.joy, self.hat)
            st.set_hat((1 if h & sdl2.SDL_HAT_RIGHT else 0) - (1 if h & sdl2.SDL_HAT_LEFT else 0),
                       (1 if h & sdl2.SDL_HAT_DOWN else 0) - (1 if h & sdl2.SDL_HAT_UP else 0))

    # ------------------------------------------------------------ force feedback
    def set_gain(self, gain):
        if self.haptic and self.query & sdl2.SDL_HAPTIC_GAIN:
            sdl2.SDL_HapticSetGain(self.haptic, int(max(0.0, min(1.0, gain)) * 100))

    def _play(self, name, effect):
        if not self.supported[name]:
            return
        eid = self.ids[name]
        if eid is None:
            eid = sdl2.SDL_HapticNewEffect(self.haptic, ctypes.byref(effect))
            if eid < 0:
                print("%s refused the %s effect (%s); playing without it" % (
                    self.device_name, name, sdl2.SDL_GetError().decode(errors="replace")), flush=True)
                self.supported[name] = False
                return
            self.ids[name] = eid
            sdl2.SDL_HapticRunEffect(self.haptic, eid, 1)
        else:
            sdl2.SDL_HapticUpdateEffect(self.haptic, eid, ctypes.byref(effect))

    def _stop_one(self, name):
        eid = self.ids[name]
        if eid is not None and self.haptic:
            sdl2.SDL_HapticStopEffect(self.haptic, eid)
            sdl2.SDL_HapticDestroyEffect(self.haptic, eid)
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
            e = sdl2.SDL_HapticEffect()
            e.type = etype
            c = e.condition
            c.type = etype
            _direction(c.direction)
            c.length = sdl2.SDL_HAPTIC_INFINITY
            c.right_sat[0] = c.left_sat[0] = 0xFFFF
            c.right_coeff[0] = c.left_coeff[0] = coef
            c.center[0] = center
            self._play(name, e)
        else:
            self._stop_one(name)
        self.last[name] = key

    def set_forces(self, constant, spring, center, damper, friction, rumble, period_ms):
        if not self.haptic:
            return
        self._condition("spring", sdl2.SDL_HAPTIC_SPRING, int(spring * 32767),
                        int(max(-1.0, min(1.0, center)) * 32767))
        self._condition("damper", sdl2.SDL_HAPTIC_DAMPER, int(damper * 32767))
        self._condition("friction", sdl2.SDL_HAPTIC_FRICTION, int(friction * 32767))
        level = int(constant * 32767)              # SDL's steering axis: positive pushes right
        if level != self.last["const"]:
            if level != 0:
                e = sdl2.SDL_HapticEffect()
                e.type = sdl2.SDL_HAPTIC_CONSTANT
                k = e.constant
                k.type = sdl2.SDL_HAPTIC_CONSTANT
                _direction(k.direction)
                k.length = sdl2.SDL_HAPTIC_INFINITY
                k.level = level
                self._play("const", e)
            else:
                self._stop_one("const")
            self.last["const"] = level
        mag = int(rumble * 32767)
        key = (mag, int(period_ms))
        if key != self.last["vib"]:
            if mag > 300:
                e = sdl2.SDL_HapticEffect()
                e.type = sdl2.SDL_HAPTIC_SINE
                p = e.periodic
                p.type = sdl2.SDL_HAPTIC_SINE
                _direction(p.direction)
                p.length = sdl2.SDL_HAPTIC_INFINITY
                p.period = int(period_ms)
                p.magnitude = mag
                self._play("vib", e)
            else:
                self._stop_one("vib")
            self.last["vib"] = key

    def close(self):
        try:
            self.stop_forces()
        finally:
            _open_instances.discard(self.inst)
            if self.haptic:
                sdl2.SDL_HapticClose(self.haptic)
            sdl2.SDL_JoystickClose(self.joy)


def list_wheels():
    _init()
    sdl2.SDL_JoystickUpdate()
    return [("%d" % i, _name_for_index(i), _looks_like_wheel(i, _name_for_index(i)))
            for i in range(sdl2.SDL_NumJoysticks())]
