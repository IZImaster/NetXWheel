"""What a backend reports about the wheel, in the same units for every wheel."""
from . import protocol as P


class WheelState:
    def __init__(self):
        self.steer = 0.0       # -1 (full left) .. +1 (full right) of the wheel's own rotation, uncentred
        self.throttle = 0.0    # 0 released .. 1 floored
        self.brake = 0.0
        self.clutch = 0.0
        self.buttons = 0       # bit mask (protocol.BUTTON_BIT)
        self.hat_x = 0         # -1 left, +1 right
        self.hat_y = 0         # -1 up, +1 down
        self.gear = 0          # -1 reverse, 0 neutral, 1..6

    def set_role(self, role, pressed):
        """A button with a role (profile) went down or up."""
        if role in P.BUTTON_BIT:
            bit = 1 << P.BUTTON_BIT[role]
            self.buttons = (self.buttons | bit) if pressed else (self.buttons & ~bit)
        if role in P.GEAR_ROLES:
            if pressed:
                self.gear = P.GEAR_ROLES[role]
            elif self.gear == P.GEAR_ROLES[role]:
                self.gear = 0
        if role in P.DPAD_ROLES:
            self._dpad[role] = pressed
            d = self._dpad
            self.hat_x = (1 if d.get("dpad_right") else 0) - (1 if d.get("dpad_left") else 0)
            self.hat_y = (1 if d.get("dpad_down") else 0) - (1 if d.get("dpad_up") else 0)

    @property
    def _dpad(self):
        if not hasattr(self, "_dpad_state"):
            self._dpad_state = {}
        return self._dpad_state


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
