"""The NetXWheel protocol, version 1 (PROTOCOL.md): packets, keys and signatures.

Used by the server and by clients (netxwheel/client.py), so both sides share one implementation.
"""
import hashlib
import hmac
import json
import os
import struct

PROTO = 1
MAGIC = b"NXW1"
DEFAULT_PORT = 27920
MAX_PACKET = 1200

# message types
DISCOVER, ANNOUNCE, CONNECT, CHALLENGE, ACCEPT, REJECT = 1, 2, 3, 4, 5, 6
STATE, FORCES, KEEPALIVE, NOTICE, BYE = 16, 17, 18, 19, 20
NAMES = {DISCOVER: "DISCOVER", ANNOUNCE: "ANNOUNCE", CONNECT: "CONNECT", CHALLENGE: "CHALLENGE",
         ACCEPT: "ACCEPT", REJECT: "REJECT", STATE: "STATE", FORCES: "FORCES",
         KEEPALIVE: "KEEPALIVE", NOTICE: "NOTICE", BYE: "BYE"}

FLAG_SIGNED = 1
HEADER = struct.Struct("<4sBBHII")        # magic, type, flags, reserved, session, seq
SIG_LEN = 16

STATE_FMT = struct.Struct("<IfffffQBbBB")  # ms, steer, throttle, brake, clutch, handbrake, buttons,
                                           # dpad, gear, status, extra_count  (36 bytes)
FORCES_FMT = struct.Struct("<B3xfffffff")  # flags, constant, spring, spring_center, damper, friction,
                                           # rumble, rumble_hz  (32 bytes)
STATUS_FORCES, STATUS_UNPLUGGED = 1, 2
FORCES_ON = 1
DPAD_UP, DPAD_RIGHT, DPAD_DOWN, DPAD_LEFT = 1, 2, 4, 8

ITERATIONS = 20000

# ---------------------------------------------------------------- buttons
# role: (bit, label). Bits 18..31 are reserved for more standard roles; 32..63 are extra1..extra32.
BUTTONS = {
    "a": (0, "A"), "b": (1, "B"), "x": (2, "X"), "y": (3, "Y"),
    "paddle_right": (4, "Right paddle"), "paddle_left": (5, "Left paddle"),
    "menu": (6, "Menu"), "view": (7, "View"), "rsb": (8, "R3"), "lsb": (9, "L3"),
    "home": (10, "Home"), "l2": (11, "L2"), "r2": (12, "R2"), "plus": (13, "+"), "minus": (14, "-"),
    "enter": (15, "Enter"), "shift_up": (16, "Shift up"), "shift_down": (17, "Shift down"),
}
EXTRA_FIRST_BIT, EXTRA_COUNT = 32, 32
for _i in range(EXTRA_COUNT):
    BUTTONS["extra%d" % (_i + 1)] = (EXTRA_FIRST_BIT + _i, "Extra %d" % (_i + 1))
STANDARD_BUTTONS = [r for r in BUTTONS if not r.startswith("extra")]
GEARS = {"reverse": -1, "gear1": 1, "gear2": 2, "gear3": 3, "gear4": 4, "gear5": 5, "gear6": 6, "gear7": 7}
DPAD = {"dpad_up": DPAD_UP, "dpad_right": DPAD_RIGHT, "dpad_down": DPAD_DOWN, "dpad_left": DPAD_LEFT}
# A wheel without a profile: its buttons in this order (no shifter: that needs a profile).
IN_ORDER = [r for r in STANDARD_BUTTONS if r not in ("shift_up", "shift_down")] + [
    "extra%d" % (i + 1) for i in range(EXTRA_COUNT)]

CMD_MAGIC = b"NXWC"     # local commands (netxwheel status, ...) from this machine: not part of the protocol


# ---------------------------------------------------------------- packets
def pack(typ, body=b"", session=0, seq=0, key=None):
    """A packet; signed when a session key is given."""
    if isinstance(body, dict):
        body = json.dumps(body, separators=(",", ":")).encode()
    flags = FLAG_SIGNED if key else 0
    data = HEADER.pack(MAGIC, typ, flags, 0, session & 0xFFFFFFFF, seq & 0xFFFFFFFF) + body
    if key:
        data += hmac.new(key, data, hashlib.sha256).digest()[:SIG_LEN]
    return data


class Packet:
    __slots__ = ("type", "flags", "session", "seq", "body", "signed", "raw")

    def json(self):
        try:
            obj = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return obj if isinstance(obj, dict) else None


def parse(data):
    """A Packet, or None if it isn't one of ours. The signature is not checked here (see verify)."""
    if len(data) < HEADER.size or data[:4] != MAGIC:
        return None
    _magic, typ, flags, _res, session, seq = HEADER.unpack_from(data)
    p = Packet()
    p.type, p.flags, p.session, p.seq, p.raw = typ, flags, session, seq, data
    p.signed = bool(flags & FLAG_SIGNED)
    end = len(data) - SIG_LEN if p.signed else len(data)
    if end < HEADER.size:
        return None
    p.body = data[HEADER.size:end]
    return p


def verify(p, key):
    """True if the packet is signed with this key."""
    if not p.signed or key is None:
        return False
    want = hmac.new(key, p.raw[:-SIG_LEN], hashlib.sha256).digest()[:SIG_LEN]
    return hmac.compare_digest(want, p.raw[-SIG_LEN:])


# ---------------------------------------------------------------- passwords and keys
def nonce():
    return os.urandom(16).hex()


def derive_key(server_password, wheel_password, salt_hex, iterations=ITERATIONS):
    secret = (server_password or "") + "\n" + (wheel_password or "")
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), bytes.fromhex(salt_hex), iterations, 32)


def proof(key, server_nonce, client_nonce, wheel):
    msg = ("proof" + server_nonce + client_nonce + str(int(wheel))).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def session_key(key, server_nonce, client_nonce):
    return hmac.new(key, ("session" + server_nonce + client_nonce).encode(), hashlib.sha256).digest()


def is_hex(s, length):
    if not isinstance(s, str) or len(s) != length:
        return False
    try:
        bytes.fromhex(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- live messages
def pack_state(ms, steer, throttle, brake, clutch, handbrake, buttons, dpad, gear, status, extra=()):
    return STATE_FMT.pack(ms & 0xFFFFFFFF, steer, throttle, brake, clutch, handbrake,
                          buttons & 0xFFFFFFFFFFFFFFFF, dpad & 0xFF, max(-1, min(7, gear)), status & 0xFF,
                          len(extra)) + struct.pack("<%df" % len(extra), *extra)


def unpack_state(body):
    if len(body) < STATE_FMT.size:
        return None
    v = STATE_FMT.unpack_from(body)
    n = v[10]
    extra = ()
    if len(body) >= STATE_FMT.size + 4 * n:
        extra = struct.unpack_from("<%df" % n, body, STATE_FMT.size)
    keys = ("ms", "steer", "throttle", "brake", "clutch", "handbrake", "buttons", "dpad", "gear", "status")
    out = dict(zip(keys, v[:10]))
    out["extra"] = list(extra)
    return out


def pack_forces(on, constant=0.0, spring=0.0, spring_center=0.0, damper=0.0, friction=0.0,
                rumble=0.0, rumble_hz=0.0):
    return FORCES_FMT.pack(FORCES_ON if on else 0, constant, spring, spring_center, damper, friction,
                           rumble, rumble_hz)


def unpack_forces(body):
    if len(body) < FORCES_FMT.size:
        return None
    flags, constant, spring, center, damper, friction, rumble, hz = FORCES_FMT.unpack_from(body)
    return {"on": bool(flags & FORCES_ON), "constant": constant, "spring": spring, "spring_center": center,
            "damper": damper, "friction": friction, "rumble": rumble, "rumble_hz": hz}
