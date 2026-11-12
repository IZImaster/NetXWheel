"""A pretend python-evdev with a pretend Logitech G920, for the tests (no wheel needed)."""
import collections
import os

AbsInfo = collections.namedtuple("AbsInfo", "value min max fuzz flat resolution")
InputEvent = collections.namedtuple("InputEvent", "type code value")


class _Codes:
    EV_KEY, EV_ABS, EV_FF = 1, 3, 21
    ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0, 1, 2, 3, 4, 5
    ABS_HAT0X, ABS_HAT0Y, ABS_HAT1X, ABS_HAT1Y = 16, 17, 18, 19
    ABS_MT_POSITION_X = 53
    BTN_TOUCH = 330
    FF_PERIODIC, FF_CONSTANT, FF_SPRING, FF_FRICTION, FF_DAMPER, FF_SINE = 0x51, 0x52, 0x53, 0x54, 0x55, 0x5a
    FF_GAIN, FF_AUTOCENTER = 0x60, 0x61


ecodes = _Codes()
ecodes.ecodes = {k: v for k, v in vars(_Codes).items() if k.isupper()}
ecodes.ecodes.update({"BTN_TRIGGER": 288})
ecodes.ABS = {v: k for k, v in vars(_Codes).items() if k.startswith("ABS_")}


class _FF:
    class Effect:
        def __init__(self, type, id, direction, trigger, replay, u):
            self.type, self.id, self.direction, self.u = type, id, direction, u

    @staticmethod
    def Condition(*a):
        return ("condition",) + a

    @staticmethod
    def Constant(level, env):
        return ("constant", level)

    @staticmethod
    def Periodic(*a):
        return ("periodic",) + a

    @staticmethod
    def Envelope(*a):
        return a

    @staticmethod
    def Trigger(*a):
        return a

    @staticmethod
    def Replay(*a):
        return a

    @staticmethod
    def EffectType(**k):
        return k


ff = _FF()


class FakeDevice:
    def __init__(self, path, name, axes, keys, ffb=True, phys="usb-1.1/input0"):
        self.path, self.name = path, name
        self.uniq, self.phys = "", phys
        self.gone = False
        self._abs = dict(axes)                    # code -> AbsInfo
        self._keys = list(keys)
        self._ffb = ffb
        self._r, self._w = os.pipe()
        self.fd = self._r
        self.queue = []
        self.log = []                             # ("upload", effect) / ("write", code, value) / ...
        self.effects = {}
        self._next = 0
        self.closed = False

    # the test drives these
    def inject(self, *events):
        for t, c, v in events:
            if t == ecodes.EV_ABS:
                a = self._abs[c]
                self._abs[c] = a._replace(value=v)
            self.queue.append(InputEvent(t, c, v))
        os.write(self._w, b"x")

    # what the backend calls
    def capabilities(self):
        caps = {ecodes.EV_ABS: [(c, a) for c, a in self._abs.items()], ecodes.EV_KEY: self._keys}
        if self._ffb:
            caps[ecodes.EV_FF] = [ecodes.FF_CONSTANT, ecodes.FF_SPRING, ecodes.FF_DAMPER, ecodes.FF_FRICTION,
                                  ecodes.FF_PERIODIC, ecodes.FF_SINE, ecodes.FF_GAIN, ecodes.FF_AUTOCENTER]
        return caps

    def absinfo(self, code):
        return self._abs[code]

    def read(self):
        if self.gone:
            raise OSError(19, "No such device")
        if not self.queue:
            raise BlockingIOError
        try:
            os.read(self._r, 4096)
        except BlockingIOError:
            pass
        out, self.queue = self.queue, []
        return out

    def write(self, etype, code, value):
        if self.gone:
            raise OSError(19, "No such device")
        self.log.append(("write", code, value))

    def upload_effect(self, effect):
        if effect.id < 0:
            self._next += 1
            effect.id = self._next
        self.effects[effect.id] = effect
        self.log.append(("upload", effect.type, effect.id, effect.u))
        return effect.id

    def erase_effect(self, eid):
        self.effects.pop(eid, None)
        self.log.append(("erase", eid))

    def close(self):
        self.closed = True


def g920(path="/dev/input/event5", phys="usb-1.1/input0"):
    axes = {ecodes.ABS_X: AbsInfo(32768, 0, 65535, 0, 0, 0)}
    for c in (ecodes.ABS_Y, ecodes.ABS_Z, ecodes.ABS_RZ):
        axes[c] = AbsInfo(255, 0, 255, 0, 0, 0)          # pedals released = 255
    axes[ecodes.ABS_HAT0X] = AbsInfo(0, -1, 1, 0, 0, 0)
    axes[ecodes.ABS_HAT0Y] = AbsInfo(0, -1, 1, 0, 0, 0)
    keys = list(range(288, 304)) + [704, 705]
    return FakeDevice(path, "Logitech G920 Driving Force Racing Wheel", axes, keys, phys=phys)


DEVICES = {}


def list_devices():
    return list(DEVICES)


def InputDevice(path):
    if path not in DEVICES:
        raise OSError(2, "No such file")
    d = DEVICES[path]
    d.closed = False
    return d
