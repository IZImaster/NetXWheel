"""What a backend reports about a wheel, in the same units for every wheel."""
from . import protocol as P


class WheelState:
    def __init__(self, extra_axes=0):
        self.steer = 0.0       # -1 (full left) .. +1 (full right) of the wheel's own rotation, uncentred
        self.throttle = 0.0    # 0 released .. 1 floored
        self.brake = 0.0
        self.clutch = 0.0
        self.handbrake = 0.0
        self.buttons = 0       # bit mask (protocol.BUTTONS)
        self.dpad = 0          # protocol.DPAD_* bits
        self.gear = 0          # -1 reverse, 0 neutral, 1..7
        self.extra = [0.0] * extra_axes
        self._dpad_buttons = 0

    def set_role(self, role, pressed):
        """A button with a role (from the profile) went down or up."""
        if role in P.BUTTONS:
            bit = 1 << P.BUTTONS[role][0]
            self.buttons = (self.buttons | bit) if pressed else (self.buttons & ~bit)
        elif role in P.GEARS:
            if pressed:
                self.gear = P.GEARS[role]
            elif self.gear == P.GEARS[role]:
                self.gear = 0
        elif role in P.DPAD:
            bit = P.DPAD[role]
            self._dpad_buttons = (self._dpad_buttons | bit) if pressed else (self._dpad_buttons & ~bit)
            self.dpad = self._dpad_buttons

    def set_hat(self, x, y):
        """A D-pad reported as a hat: x -1 left .. +1 right, y -1 up .. +1 down."""
        d = 0
        if x < 0:
            d |= P.DPAD_LEFT
        if x > 0:
            d |= P.DPAD_RIGHT
        if y < 0:
            d |= P.DPAD_UP
        if y > 0:
            d |= P.DPAD_DOWN
        self.dpad = d


def pedal(value, lo, hi, rest_high):
    """A pedal axis value as 0 (released) .. 1 (floored)."""
    span = float(hi - lo) or 1.0
    v = (value - lo) / span
    if rest_high:
        v = 1.0 - v
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def axis_centered(value, lo, hi):
    """A steering axis value as -1 .. +1."""
    mid = (lo + hi) / 2.0
    half = (hi - lo) / 2.0 or 1.0
    v = (value - mid) / half
    return -1.0 if v < -1 else 1.0 if v > 1 else v


def axis_unit(value, lo, hi):
    """An extra axis (dial, slider) as 0 .. 1."""
    span = float(hi - lo) or 1.0
    v = (value - lo) / span
    return 0.0 if v < 0 else 1.0 if v > 1 else v
