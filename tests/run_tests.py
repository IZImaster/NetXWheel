#!/usr/bin/env python3
"""NetXWheel's tests, with pretend wheels (no wheel or evdev needed):  python3 tests/run_tests.py"""
import glob
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "fake"))   # the pretend evdev
sys.path.insert(0, REPO)
HOME = tempfile.mkdtemp(prefix="netxwheel-test-")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME

import evdev  # noqa: E402  (the fake one)
from netxwheel import core, profiles, protocol as P, settings as S  # noqa: E402
from netxwheel.client import Connection, ConnectError, discover  # noqa: E402

E = evdev.ecodes
core.SCAN_EVERY = 0.4          # look for wheels more often in the tests


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def command(port, text):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    s.sendto(P.CMD_MAGIC + text.encode(), ("127.0.0.1", port))
    try:
        return s.recvfrom(8192)[0].decode()
    except socket.timeout:
        return ""
    finally:
        s.close()


def deg(d, rotation=900):
    """The raw axis value of a wheel turned d degrees right."""
    return 32768 + int(d / (rotation / 2.0) * 32767)


def wait_for(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def acme(path="/dev/input/event7"):
    d = evdev.g920(path, phys="usb-1.2/input0")
    d.name = "Acme Racing Wheel Pro"
    return d


# ------------------------------------------------------------------ pieces
class TestProtocol(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(P.HEADER.size, 16)
        self.assertEqual(P.STATE_FMT.size, 36)
        self.assertEqual(P.FORCES_FMT.size, 32)

    def test_signing(self):
        k = P.session_key(P.derive_key("a", "b", "00" * 16), "11" * 16, "22" * 16)
        pk = P.pack(P.STATE, P.pack_state(1, 0.5, 0, 0, 0, 0, 1 << 40, 3, 2, 1, [0.25]), 7, 9, k)
        p = P.parse(pk)
        self.assertTrue(P.verify(p, k))
        self.assertFalse(P.verify(p, b"x" * 32))
        bad = bytearray(pk)
        bad[20] ^= 1
        self.assertFalse(P.verify(P.parse(bytes(bad)), k))
        st = P.unpack_state(p.body)
        self.assertEqual(st["buttons"], 1 << 40)
        self.assertEqual(st["extra"], [0.25])

    def test_passwords_change_the_key(self):
        a = P.derive_key("x", "", "00" * 16)
        self.assertNotEqual(a, P.derive_key("y", "", "00" * 16))
        self.assertNotEqual(a, P.derive_key("x", "", "01" * 16))
        self.assertNotEqual(a, P.derive_key("", "x", "00" * 16))


class TestProfiles(unittest.TestCase):
    def test_shipped_profiles_are_valid(self):
        files = glob.glob(os.path.join(REPO, "profiles", "*.ini"))
        self.assertTrue(files)
        for f in files:
            self.assertEqual(profiles.validate(f), [], f)

    def test_g920(self):
        p = profiles.choose("Logitech G920 Driving Force Racing Wheel")
        self.assertEqual((p.id, p.rotation, p.vendor, p.torque_nm, p.direct_drive),
                         ("logitech-g920", 900, "Logitech", 2.3, False))
        self.assertEqual(profiles.choose("Some Other Wheel").id, "generic")


class TestSettings(unittest.TestCase):
    def test_write_keeps_the_rest(self):
        S.ensure()
        S.write("gain", "0.45")
        with open(S.path()) as f:
            text = f.read()
        self.assertIn("# overall force", text)
        self.assertEqual(S.Settings().gain, 0.45)
        S.write("gain", "0.6")

    def test_bad_values_are_refused(self):
        for name, value in (("gain", "2"), ("gain", "loud"), ("nope", "1"), ("invert_force", "maybe"),
                            ("steer_degrees", "5"), ("name", 'a"b'), ("name", "x" * 70)):
            with self.assertRaises(ValueError):
                S.write(name, value)

    def test_wheel_settings(self):
        S.write_wheel("W@1", "password", "pw", "A wheel")
        S.write_wheel("W@1", "center", "0.01")
        self.assertEqual(S.wheel("W@1")["password"], "pw")
        self.assertEqual(S.wheel("W@1")["center"], "0.01")
        S.write_wheel("W@1", "center", "")
        self.assertEqual(S.wheel("W@1")["center"], "0.0")
        self.assertEqual(S.wheel("other")["password"], "")


class TestSteering(unittest.TestCase):
    def setUp(self):
        cfg = S.Settings(dict((n, v) for n, v, _ in S.DEFAULTS))
        self.st = core.Steering(cfg, 900, None, 0.0)

    def test_game_full_lock_at_half_of_steer_degrees(self):
        self.assertAlmostEqual(self.st.game(90 / 450.0), 0.5, places=3)
        self.assertAlmostEqual(self.st.game(180 / 450.0), 1.0, places=3)
        self.assertEqual(self.st.game(300 / 450.0), 1.0)
        self.assertAlmostEqual(self.st.game_to_wheel(0.5), 0.2, places=3)

    def test_warning_vibration_follows_the_real_wheel(self):
        self.assertEqual(self.st.idle(180 / 450.0)[1], 0.0)
        self.assertEqual(self.st.idle(370 / 450.0)[1], 0.0)
        self.assertAlmostEqual(self.st.idle(374 / 450.0)[1], 0.25, places=1)
        self.assertGreater(self.st.idle(440 / 450.0)[1], 0.5)
        self.assertEqual(self.st.idle(449 / 450.0)[1], 0.0)

    def test_nonsense_numbers(self):
        self.assertEqual(core.clamp(float("nan"), -1.0, 1.0), 0.0)
        self.assertEqual(core.clamp(float("inf"), 0.0, 1.0), 0.0)


# ------------------------------------------------------------------ the whole server
class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        S.ensure()
        S.write("port", str(cls.port))
        S.write("name", "Test Pi")
        S.write("server_password", "")
        cls.g = evdev.g920()
        cls.a = acme()
        evdev.DEVICES.clear()
        evdev.DEVICES[cls.g.path] = cls.g
        evdev.DEVICES[cls.a.path] = cls.a
        threading.Thread(target=core.main, args=([],), daemon=True).start()
        assert wait_for(lambda: "wheel 2" in command(cls.port, "status"), 5), "the server found both wheels"

    def connect(self, wheel=1, spw="", wpw="", name="test game"):
        c = Connection(("127.0.0.1", self.port), wheel, spw, wpw, client=name)
        c.connect()
        self.addCleanup(c.bye)
        return c

    def uploads(self, dev, etype):
        return [e for e in dev.log if e[0] == "upload" and e[1] == etype]

    def settle(self):
        time.sleep(1.3)                  # the server re-reads settings once a second

    def wheel_line(self, n):
        return command(self.port, "status").split("\n")[n]

    # --- finding
    def test_01_discover(self):
        found = discover(["127.0.0.1"], self.port, 0.6)
        self.assertEqual(len(found), 1)
        a = found[0][1]
        self.assertEqual(a["name"], "Test Pi")
        self.assertEqual([w["index"] for w in a["wheels"]], [1, 2])
        w1, w2 = a["wheels"]
        self.assertEqual((w1["vendor"], w1["model"], w1["rotation"], w1["steer_range"], w1["torque_nm"]),
                         ("Logitech", "G920 Driving Force", 900, 360, 2.3))
        self.assertEqual(w1["shifter"], "h6r")
        self.assertEqual(w1["pedals"], ["throttle", "brake", "clutch"])
        self.assertTrue(w1["ffb"])
        self.assertEqual(w2["model"], "Acme Racing Wheel Pro")
        self.assertEqual(w2["shifter"], "none")         # generic: no gears from random buttons

    def test_02_discover_is_rate_limited(self):
        time.sleep(1.1)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        for _ in range(10):
            s.sendto(P.pack(P.DISCOVER, {"proto": 1}), ("127.0.0.1", self.port))
        got = 0
        try:
            while True:
                s.recvfrom(4096)
                got += 1
        except socket.timeout:
            pass
        s.close()
        self.assertEqual(got, 4)

    # --- driving
    def test_10_state_and_buttons(self):
        c = self.connect()
        w = c.wheel
        self.assertIn({"bit": 10, "role": "home", "label": "Xbox"}, w["buttons"])
        self.assertIn("damper", w["forces"])
        self.g.inject((E.EV_ABS, E.ABS_X, deg(90)), (E.EV_ABS, E.ABS_Y, 0), (E.EV_KEY, 288, 1),
                      (E.EV_KEY, 300, 1), (E.EV_ABS, E.ABS_HAT0X, -1))
        st = None
        end = time.time() + 1
        while time.time() < end:
            st = c.poll(0.05) or st
        self.assertAlmostEqual(st["steer"], 0.5, places=2)
        self.assertAlmostEqual(st["throttle"], 1.0, places=2)
        self.assertEqual(st["buttons"] & 1, 1)
        self.assertEqual(st["gear"], 1)
        self.assertEqual(st["dpad"], P.DPAD_LEFT)
        self.assertIn("used by test game", self.wheel_line(1))
        self.g.inject((E.EV_ABS, E.ABS_X, 32768), (E.EV_ABS, E.ABS_Y, 255), (E.EV_KEY, 288, 0),
                      (E.EV_KEY, 300, 0), (E.EV_ABS, E.ABS_HAT0X, 0))

    def test_11_forces(self):
        c = self.connect()
        self.g.log.clear()
        c.send_forces(True, constant=0.4, spring=0.5, spring_center=0.5, damper=0.3, friction=0.2,
                      rumble=1.0, rumble_hz=50)
        self.assertTrue(wait_for(lambda: self.uploads(self.g, E.FF_PERIODIC)))
        const = self.uploads(self.g, E.FF_CONSTANT)[-1][3]["ff_constant_effect"][1]
        self.assertAlmostEqual(const, -int(0.4 * 32767), delta=2)        # positive right -> kernel negative
        spring = self.uploads(self.g, E.FF_SPRING)[-1][3]["ff_condition_effect"][0]
        self.assertEqual(spring[3], int(0.5 * 32767))
        self.assertAlmostEqual(spring[6], int(0.2 * 32767), delta=2)     # 0.5 of 360 degrees = 0.2 of 900
        self.assertTrue(self.uploads(self.g, E.FF_DAMPER) and self.uploads(self.g, E.FF_FRICTION))
        per = self.uploads(self.g, E.FF_PERIODIC)[-1][3]["ff_periodic_effect"]
        self.assertEqual(per[2], 20)                                     # 50 Hz -> 20 ms
        self.assertAlmostEqual(per[3], int(0.3 * 32767), delta=2)       # rumble_strength 0.3
        # the forces stop by themselves when FORCES stop arriving
        self.g.log.clear()
        time.sleep(0.6)
        self.assertTrue([e for e in self.g.log if e[0] == "erase"])
        # nonsense numbers don't break anything
        c.send_forces(True, constant=float("nan"), spring=float("inf"))
        time.sleep(0.1)
        self.assertIn("used by", self.wheel_line(1))

    def test_12_forged_and_replayed_packets_are_ignored(self):
        c = self.connect()
        c.send_forces(True, constant=0.1)
        time.sleep(0.1)
        self.g.log.clear()
        # signed with a wrong key
        c.sock.sendto(P.pack(P.FORCES, P.pack_forces(True, constant=-0.9), c.session, c.seq_out + 5, b"k" * 32), c.addr)
        # unsigned
        c.sock.sendto(P.pack(P.FORCES, P.pack_forces(True, constant=-0.9), c.session, c.seq_out + 6), c.addr)
        # a real packet number sent again (replay)
        c.seq_out -= 1
        c.send_forces(True, constant=-0.9)
        time.sleep(0.2)
        self.assertFalse(self.uploads(self.g, E.FF_CONSTANT), self.g.log)
        # from another address, even with the right key
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        other.sendto(P.pack(P.FORCES, P.pack_forces(True, constant=-0.9), c.session, c.seq_out + 10, c.key), c.addr)
        other.close()
        time.sleep(0.2)
        self.assertFalse(self.uploads(self.g, E.FF_CONSTANT))

    def test_13_in_use_and_two_games(self):
        c1 = self.connect(1, name="game one")
        with self.assertRaises(ConnectError) as e:
            self.connect(1, name="game two")
        self.assertEqual(e.exception.reason, "in_use")
        self.assertIn("game one", e.exception.message)
        c2 = self.connect(2, name="game two")                  # the other wheel is free
        self.assertEqual(c2.wheel["index"], 2)
        found = discover(["127.0.0.1"], self.port, 0.5)[0][1]
        self.assertTrue(all(w["in_use"] for w in found["wheels"]))
        c1.bye()
        c2.bye()
        self.assertTrue(wait_for(lambda: "free" in self.wheel_line(1) and "free" in self.wheel_line(2)))

    def test_14_no_such_wheel(self):
        with self.assertRaises(ConnectError) as e:
            self.connect(7)
        self.assertEqual(e.exception.reason, "no_wheel")

    def test_15_timeout_frees_the_wheel(self):
        c = Connection(("127.0.0.1", self.port), 2, client="silent game")
        c.connect()
        self.assertIn("silent game", self.wheel_line(2))
        self.assertTrue(wait_for(lambda: "silent game" not in self.wheel_line(2), 5))

    def test_16_kick(self):
        c = self.connect(2)
        self.assertIn("free again", command(self.port, "kick 2"))
        self.assertTrue(wait_for(lambda: (c.poll(0.05), c.closed)[1], 2))
        self.assertTrue(any(n.get("event") == "kicked" for n in c.notices))

    def test_17_buzz_and_idle_warning(self):
        self.a.log.clear()
        self.assertIn("vibrating", command(self.port, "buzz 2 0.8"))
        self.assertTrue(wait_for(lambda: self.uploads(self.a, E.FF_PERIODIC)))
        self.g.log.clear()
        self.g.inject((E.EV_ABS, E.ABS_X, deg(420)))                  # near the end stop: warning
        self.assertTrue(wait_for(lambda: self.uploads(self.g, E.FF_PERIODIC)))
        self.g.inject((E.EV_ABS, E.ABS_X, 32768))

    def test_18_center_per_wheel(self):
        self.g.inject((E.EV_ABS, E.ABS_X, deg(9)))
        time.sleep(0.1)
        self.assertIn("centre saved", command(self.port, "center 1"))
        self.assertIn(" 0% ", self.wheel_line(1))
        self.assertIn(" 0% ", self.wheel_line(2))
        self.assertIn("back to the wheel's own", command(self.port, "reset 1"))
        self.g.inject((E.EV_ABS, E.ABS_X, 32768))

    # --- passwords
    def test_20_server_password(self):
        S.write("server_password", "secret")
        self.settle()
        try:
            with self.assertRaises(ConnectError) as e:
                self.connect(1)
            self.assertEqual(e.exception.reason, "password")
            c = self.connect(1, spw="secret")
            self.assertTrue(c.key)
            self.assertTrue(discover(["127.0.0.1"], self.port, 0.5)[0][1]["password"])
            c.bye()
            time.sleep(0.2)
        finally:
            S.write("server_password", "")
            self.settle()

    def test_40_wheel_password_and_lockout(self):
        uid = command(self.port, "uid 2").split("\n")[0]
        S.write_wheel(uid, "password", "w2pw", "Acme")
        self.settle()
        try:
            self.connect(1).bye()                                 # wheel 1 has no password
            time.sleep(0.1)
            reasons = []
            for _ in range(7):
                try:
                    self.connect(2, wpw="wrong")
                    reasons.append("accepted")
                except ConnectError as e:
                    reasons.append(e.reason)
            self.assertNotIn("accepted", reasons)
            self.assertEqual(reasons[0], "password")
            self.assertEqual(reasons[-1], "busy")                # locked out for a minute after 5 wrong ones
            self.assertLessEqual(reasons.count("password"), 5)
            with self.assertRaises(ConnectError) as e:            # even the right password now
                self.connect(2, wpw="w2pw")
            self.assertEqual(e.exception.reason, "busy")
        finally:
            S.write_wheel(uid, "password", "", "Acme")
            self.settle()

    # --- unplugging
    def test_19_unplug_and_back(self):
        c = self.connect(1)
        self.g.gone = True
        del evdev.DEVICES[self.g.path]
        os.write(self.g._w, b"x")                                  # wake the server up
        self.assertTrue(wait_for(lambda: (c.poll(0.05), any(n.get("event") == "unplugged" for n in c.notices))[1]))
        st = None
        end = time.time() + 0.3
        while time.time() < end:
            st = c.poll(0.05) or st
        self.assertTrue(st and st["status"] & P.STATUS_UNPLUGGED)
        self.assertIn("UNPLUGGED", self.wheel_line(1))
        back = evdev.g920()                                        # the same wheel, same USB port
        evdev.DEVICES[back.path] = back
        self.assertTrue(wait_for(lambda: (c.keepalive(), c.poll(0.05),
                                          any(n.get("event") == "plugged" for n in c.notices))[2], 4))
        self.assertIn("wheel 1: Logitech", self.wheel_line(1))
        type(self).g = back


if __name__ == "__main__":
    unittest.main(verbosity=2)
