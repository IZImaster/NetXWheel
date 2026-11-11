"""Pretends to be a game: finds servers, connects to a wheel and shows it as a game sees it.

  netxwheel test                      the servers on the network: pick one, then a wheel
  netxwheel test 192.168.1.50         that server
  netxwheel test 192.168.1.50 2       its wheel 2
  netxwheel test ... ffb              also play gentle test forces for 15 seconds
"""
import getpass
import math
import time

from . import protocol as P, settings as S
from .client import Connection, ConnectError, discover


def pick(prompt, count):
    while True:
        a = input(prompt).strip()
        if not a and count == 1:
            return 0
        if a.isdigit() and 1 <= int(a) <= count:
            return int(a) - 1
        print("  type a number from 1 to %d" % count)


def main(args):
    play = "ffb" in args
    rest = [a for a in args if a != "ffb"]
    host = rest[0] if rest else None
    wheel = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None
    port = S.Settings().port if host in (None, "127.0.0.1", "localhost") else P.DEFAULT_PORT

    print("Looking for servers..." if host is None else "Asking %s..." % host)
    found = discover(["255.255.255.255", "127.0.0.1"] if host is None else [host], port=port, seconds=1.5)
    if not found:
        print("No server answered. Is it running (netxwheel status), and on the same network?")
        return 1
    if len(found) > 1:
        for i, (addr, a) in enumerate(found):
            print("  %d. %s  (%s)%s" % (i + 1, a.get("name"), addr[0], "  password" if a.get("password") else ""))
        addr, ann = found[pick("Which server? ", len(found))]
    else:
        addr, ann = found[0]
    print('Server "%s" at %s, %s' % (ann.get("name"), addr[0], ann.get("software")))
    wheels = ann.get("wheels", [])
    if not wheels:
        print("It has no wheel right now.")
        return 1
    for w in wheels:
        bits = ["%s %s" % (w.get("vendor", ""), w.get("model", "")), "%d degrees" % w.get("rotation", 0),
                "full lock at %d" % w.get("steer_range", 0), "force feedback" if w.get("ffb") else "no forces"]
        if w.get("direct_drive"):
            bits.append("direct drive")
        if w.get("torque_nm"):
            bits.append("%.1f Nm" % w["torque_nm"])
        if w.get("password"):
            bits.append("password")
        if w.get("connected") is False:
            bits.append("UNPLUGGED")
        if w.get("in_use"):
            bits.append("in use by %s" % w.get("in_use_by"))
        print("  wheel %d: %s" % (w["index"], ", ".join(b.strip() for b in bits)))
    if wheel is None:
        wheel = wheels[pick("Which wheel? " if len(wheels) > 1 else "Press Enter to connect... ", len(wheels))]["index"]

    spw = getpass.getpass("Server password: ") if ann.get("password") else ""
    w = next((x for x in wheels if x["index"] == wheel), {})
    wpw = getpass.getpass("Wheel %d's password: " % wheel) if w.get("password") else ""
    c = Connection(addr, wheel, spw, wpw, client="netxwheel test")
    try:
        a = c.connect()
    except ConnectError as e:
        print("Not connected:", e.message)
        return 1
    d = a["wheel"]
    print("Connected to wheel %d: %s %s. Buttons: %s" % (
        d["index"], d.get("vendor", ""), d.get("model", ""), ", ".join(b["label"] for b in d.get("buttons", [])) or "-"))
    print("Ctrl+C to stop")
    labels = {b["bit"]: b["label"] for b in d.get("buttons", [])}
    start = last_keep = last_print = time.time()
    try:
        while not c.closed:
            st = c.poll(0.02)
            now = time.time()
            if play and now - start < 15:
                c.send_forces(True, constant=0.2 * math.sin((now - start) * math.pi), spring=0.25)
            elif now - last_keep > 0.5:
                last_keep = now
                c.keepalive()
            for n in c.notices:
                print("\nserver says:", n)
            c.notices.clear()
            if st and now - last_print > 0.1:
                last_print = now
                pressed = [labels.get(b, "#%d" % b) for b in range(64) if st["buttons"] & (1 << b)]
                gear = "R" if st["gear"] < 0 else ("N" if st["gear"] == 0 else str(st["gear"]))
                dp = "".join(ch for bit, ch in ((1, "U"), (2, "R"), (4, "D"), (8, "L")) if st["dpad"] & bit) or "-"
                line = "steer %+5.2f  gas %.2f  brake %.2f  clutch %.2f  gear %s  dpad %s  %s%s" % (
                    st["steer"], st["throttle"], st["brake"], st["clutch"], gear, dp, " ".join(pressed),
                    "  (UNPLUGGED)" if st["status"] & P.STATUS_UNPLUGGED else "")
                print("\r" + line[:150].ljust(150), end="", flush=True)
            if time.monotonic() - c.last_rx > 2.0:
                print("\nthe server stopped answering")
                break
    except KeyboardInterrupt:
        print()
    finally:
        c.bye()
    return 0
