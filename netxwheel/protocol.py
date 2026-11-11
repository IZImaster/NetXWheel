"""The NetXWheel protocol, version 1 (PROTOCOL.md). These numbers never change."""
import struct

VERSION = 1
MAGIC = b"GT3W"            # (historical: the protocol began in GT3 Revisited) fixed forever
DEFAULT_PORT = 27920

T_HELLO, T_HELLO_ACK, T_STATE, T_FFB, T_BYE = 1, 2, 3, 4, 5

HDR = struct.Struct("<4sBBH")           # magic, version, type, sequence
ACK = struct.Struct("<BH32s")           # capabilities, steering range in degrees, device name
STATE = struct.Struct("<IffffIbbbB")    # ms, steer, throttle, brake, clutch, buttons, hat x, hat y, gear, pad
FFB = struct.Struct("<ffffffffBhh")     # speed, brake, abs, steer_force, jolt, traction,
                                        # understeer, oversteer, flags, spring, constant
CAP_FFB, CAP_SHIFTER = 1, 2             # HELLO_ACK capability bits
FLAG_WHEEL_FORCE = 1                    # FFB flags bit 0: wheel force enabled in the game

# Button roles and their bit in STATE.buttons. Bits 18..31 are extra buttons a wheel has beyond these.
BUTTON_ROLES = [
    "a", "b", "x", "y",                 # 0..3
    "paddle_right", "paddle_left",      # 4, 5
    "menu", "view", "rsb", "lsb", "home",  # 6..10
    "reverse",                          # 11 (H-shifter)
    "gear1", "gear2", "gear3", "gear4", "gear5", "gear6",  # 12..17 (H-shifter)
]
BUTTON_BIT = {name: i for i, name in enumerate(BUTTON_ROLES)}
EXTRA_FIRST_BIT, EXTRA_COUNT = 18, 14   # extra1..extra14 -> bits 18..31
for _i in range(EXTRA_COUNT):
    BUTTON_BIT["extra%d" % (_i + 1)] = EXTRA_FIRST_BIT + _i
DPAD_ROLES = ["dpad_up", "dpad_down", "dpad_left", "dpad_right"]   # buttons that make the hat
# A wheel without a profile: its buttons in order get these (no shifter gears: those need a profile).
IN_ORDER_ROLES = [r for r in BUTTON_ROLES if not (r == "reverse" or r.startswith("gear"))] + [
    "extra%d" % (i + 1) for i in range(EXTRA_COUNT)]
GEAR_ROLES = {"reverse": -1, "gear1": 1, "gear2": 2, "gear3": 3, "gear4": 4, "gear5": 5, "gear6": 6}

CMD_MAGIC = b"NXWC"     # local commands (netxwheel status, center, ...); not part of the protocol


def header(typ, seq):
    return HDR.pack(MAGIC, VERSION, typ, seq & 0xFFFF)
