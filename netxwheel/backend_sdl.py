"""Windows (and macOS/Linux, if asked) backend: the wheel and its forces through SDL2 (pysdl2).

Install:  pip install pysdl2 pysdl2-dll
The effects are built like GT3 Revisited's own wheel code: on the steering axis, one infinite play.
"""
import ctypes

try:
    import sdl2
except ImportError as e:  # pragma: no cover - depends on the machine
    raise SystemExit("NetXWheel needs pysdl2 on this system: pip install pysdl2 pysdl2-dll (%s)" % e)

from . import protocol as P
from .profiles import AXIS_ROLES, ALL_BUTTON_ROLES, choose
from .state import WheelState, axis_centered, pedal

NAME = "SDL"
_ready = False


def _init():
    global _ready
    if _ready:
        return
    sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")
    if sdl2.SDL_Init(sdl2.SDL_INIT_JOYSTICK | sdl2.SDL_INIT_HAPTIC) != 0:
        raise SystemExit("SDL could not start: %s" % sdl2.SDL_GetError().decode(errors="replace"))
    sdl2.SDL_JoystickEventState(sdl2.SDL_IGNORE)   # read the wheel directly, no event queue
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


def open_wheel(cfg, all_profiles):
    _init()
    sdl2.SDL_JoystickUpdate()
    count = sdl2.SDL_NumJoysticks()
    names = [_name_for_index(i) for i in range(count)]
    wanted = cfg.profile if cfg.profile != "auto" else None
    pick = None
    for i, name in enumerate(names):       # 1. a device a profile names
        p = choose(name, cfg.profile, all_profiles)
        if p.id != "generic" and "windows" in p.sections and (p.matches(name) or wanted == p.id):
            pick = (i, p)
            break
    if pick is None:                       # 2. anything that looks like a wheel
        for i, name in enumerate(names):
            if _looks_like_wheel(i, name):
                pick = (i, choose(name, "auto", []))
                break
    if pick is None:
        return None, None
    joy = sdl2.SDL_JoystickOpen(pick[0])
    if not joy:
        return None, None
    return SdlWheel(joy, pick[1]), pick[1]


def _direction(e_dir):
    steering = getattr(sdl2, "SDL_HAPTIC_STEERING_AXIS", None)
    if steering is not None:
        e_dir.type = steering
    else:
        e_dir.type = sdl2.SDL_HAPTIC_CARTESIAN
        e_dir.dir[0] = 1


class SdlWheel:
    name = NAME

    def __init__(self, joy, profile):
        self.joy = joy
        n = sdl2.SDL_JoystickName(joy)
        self.device_name = n.decode("utf-8", "replace") if n else "wheel"
        self.state = WheelState()
        m = profile.platform("windows")
        rest = m.get("pedals_rest", "auto")
        naxes = sdl2.SDL_JoystickNumAxes(joy)
        self.axes = []                                    # (index, role, rest_high)
        for role in AXIS_ROLES:
            idx = _num(m.get(role, ""), "axis")
            if idx is None or idx >= naxes:
                continue
            value = sdl2.SDL_JoystickGetAxis(joy, idx)
            rest_high = rest == "high" or (rest == "auto" and role != "steering" and value > 0)
            self.axes.append((idx, role, rest_high))
        self.hat = _num(m.get("hat", "hat0"), "hat")
        if self.hat is not None and self.hat >= sdl2.SDL_JoystickNumHats(joy):
            self.hat = None
        nbuttons = sdl2.SDL_JoystickNumButtons(joy)
        self.buttons = {}                                 # index -> role
        if m.get("buttons") == "in_order":
            roles = P.IN_ORDER_ROLES
            for i in range(min(nbuttons, len(roles))):
                self.buttons[i] = roles[i]
        for role in ALL_BUTTON_ROLES:
            if role in m:
                idx = _num(m[role], "button")
                if idx is not None and idx < nbuttons:
                    self.buttons[idx] = role
        self.pressed = {}
        self.has_shifter = any(r in P.GEAR_ROLES for r in self.buttons.values())
        # force feedback
        self.haptic = sdl2.SDL_HapticOpenFromJoystick(joy)
        q = sdl2.SDL_HapticQuery(self.haptic) if self.haptic else 0
        self.query = q
        self.supported = {"spring": bool(q & sdl2.SDL_HAPTIC_SPRING),
                          "const": bool(q & sdl2.SDL_HAPTIC_CONSTANT),
                          "vib": bool(q & sdl2.SDL_HAPTIC_SINE)}
        self.has_ffb = self.supported["spring"] or self.supported["const"]
        self.ids = {"spring": None, "const": None, "vib": None}
        self.last = {"spring": None, "const": None, "vib": None}
        if self.haptic and q & sdl2.SDL_HAPTIC_AUTOCENTER:
            sdl2.SDL_HapticSetAutocenter(self.haptic, 0)

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
            else:
                setattr(st, role, pedal(v, -32768, 32767, rest_high))
        for idx, role in self.buttons.items():
            down = bool(sdl2.SDL_JoystickGetButton(self.joy, idx))
            if self.pressed.get(idx) != down:
                self.pressed[idx] = down
                st.set_role(role, down)
        if self.hat is not None:
            h = sdl2.SDL_JoystickGetHat(self.joy, self.hat)
            st.hat_x = (1 if h & sdl2.SDL_HAT_RIGHT else 0) - (1 if h & sdl2.SDL_HAT_LEFT else 0)
            st.hat_y = (1 if h & sdl2.SDL_HAT_DOWN else 0) - (1 if h & sdl2.SDL_HAT_UP else 0)

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
                print("this wheel refused the %s effect (%s); playing without it" % (
                    name, sdl2.SDL_GetError().decode(errors="replace")), flush=True)
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

    def set_forces(self, spring, center, push, vib):
        """spring 0..1, center -1..1, push -1..1 (positive turns right), vib 0..1."""
        if not self.haptic:
            return
        coef = int(spring * 32767)
        center_ff = int(max(-1.0, min(1.0, center)) * 32767)
        level = int(push * 32767)              # SDL's steering axis: positive pushes right
        mag = int(vib * 32767)

        key = (coef, center_ff)
        if key != self.last["spring"]:
            if coef > 0:
                e = sdl2.SDL_HapticEffect()
                e.type = sdl2.SDL_HAPTIC_SPRING
                c = e.condition
                c.type = sdl2.SDL_HAPTIC_SPRING
                _direction(c.direction)
                c.length = sdl2.SDL_HAPTIC_INFINITY
                c.right_sat[0] = c.left_sat[0] = 0xFFFF
                c.right_coeff[0] = c.left_coeff[0] = coef
                c.center[0] = center_ff
                self._play("spring", e)
            else:
                self._stop_one("spring")
            self.last["spring"] = key

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

        if mag != self.last["vib"]:
            if mag > 300:
                e = sdl2.SDL_HapticEffect()
                e.type = sdl2.SDL_HAPTIC_SINE
                p = e.periodic
                p.type = sdl2.SDL_HAPTIC_SINE
                _direction(p.direction)
                p.length = sdl2.SDL_HAPTIC_INFINITY
                p.period = 40
                p.magnitude = mag
                self._play("vib", e)
            else:
                self._stop_one("vib")
            self.last["vib"] = mag

    def close(self):
        try:
            self.stop_forces()
        finally:
            if self.haptic:
                sdl2.SDL_HapticClose(self.haptic)
            sdl2.SDL_JoystickClose(self.joy)


def list_wheels():
    _init()
    sdl2.SDL_JoystickUpdate()
    return [("%d" % i, _name_for_index(i), _looks_like_wheel(i, _name_for_index(i)))
            for i in range(sdl2.SDL_NumJoysticks())]
