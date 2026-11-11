#!/usr/bin/env python3
"""NetXWheel's tests, with a pretend G920 (no wheel or evdev needed):  python3 tests/run_tests.py"""
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


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Game:
    """A pretend game (client)."""

    def __init__(self, port):
        self.addr = ("127.0.0.1", port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self.seq = 0

    def send(self, typ, payload=b""):
        self.seq += 1
        self.sock.sendto(P.header(typ, self.seq) + payload, self.addr)

    def recv(self, want, tries=50):
        for _ in range(tries):
            data, _ = self.sock.recvfrom(1500)
            _m, ver, typ, _s = P.HDR.unpack_from(data)
            if typ == want:
                return data[P.HDR.size:]
        raise AssertionError("no message of type %d" % want)

    def latest_state(self, seconds=0.15):
        """The newest STATE within a short time (the server sends 100 a second)."""
        st = None
        end = time.time() + seconds
        self.sock.settimeout(0.05)
        while time.time() < end:
            try:
                data, _ = self.sock.recvfrom(1500)
            except socket.timeout:
                continue
            if data[5] == P.T_STATE:
                st = P.STATE.unpack_from(data, P.HDR.size)
        self.sock.settimeout(1.0)
        return st


def command(port, text):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    s.sendto(P.CMD_MAGIC + text.encode(), ("127.0.0.1", port))
    try:
        return s.recvfrom(2048)[0].decode()
    finally:
        s.close()


class TestProfiles(unittest.TestCase):
    def test_shipped_profiles_are_valid(self):
        files = glob.glob(os.path.join(REPO, "profiles", "*.ini"))
        self.assertTrue(files)
        for f in files:
            self.assertEqual(profiles.validate(f), [], f)

    def test_g920_is_chosen(self):
        p = profiles.choose("Logitech G920 Driving Force Racing Wheel")
        self.assertEqual(p.id, "logitech-g920")
        self.assertEqual(p.rotation, 900)
        self.assertEqual(profiles.choose("Some Other Wheel").id, "generic")


class TestSettings(unittest.TestCase):
    def test_write_keeps_the_rest(self):
        S.ensure()
        S.write("gain", "0.45")
        with open(S.path()) as f:
            text = f.read()
        self.assertIn("# overall force", text)
        self.assertEqual(S.read()["gain"], "0.45")
        self.assertEqual(S.Settings().gain, 0.45)
        S.write("gain", "0.6")

    def test_bad_values_are_refused(self):
        for name, value in (("gain", "2"), ("gain", "loud"), ("nope", "1"), ("invert_force", "maybe"),
                            ("steer_degrees", "5"), ("profile", "a b")):
            with self.assertRaises(ValueError):
                S.write(name, value)
        self.assertEqual(S.write("invert_force", "Yes"), "yes")
        S.write("invert_force", "no")


class TestSteering(unittest.TestCase):
    def setUp(self):
        self.cfg = S.Settings(dict((n, v) for n, v, _ in S.DEFAULTS))
        self.st = core.Steering(self.cfg, profiles.choose("G920"))

    def deg(self, d):
        return d / 450.0                      # G920: 900 lock-to-lock, 450 each way

    def test_game_full_lock_at_half_of_steer_degrees(self):
        self.assertAlmostEqual(self.st.game(self.deg(90)), 0.5, places=3)
        self.assertAlmostEqual(self.st.game(self.deg(180)), 1.0, places=3)
        self.assertEqual(self.st.game(self.deg(300)), 1.0)
        self.assertAlmostEqual(self.st.game(-self.deg(90)), -0.5, places=3)

    def test_warning_vibration_follows_the_real_wheel(self):
        self.assertEqual(self.st.idle(self.deg(180))[1], 0.0)         # game full lock: quiet
        self.assertEqual(self.st.idle(self.deg(370))[1], 0.0)         # below 83 %
        self.assertAlmostEqual(self.st.idle(self.deg(374))[1], 0.25, places=1)
        self.assertGreater(self.st.idle(self.deg(440))[1], 0.5)
        self.assertEqual(self.st.idle(self.deg(449))[1], 0.0)         # the end stop: quiet
        self.assertAlmostEqual(self.st.idle(0)[0], 0.70)


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        S.ensure()
        S.write("port", str(cls.port))
        with open(os.path.join(HOME, ".wheel_center"), "w") as f:
            f.write("33000")                  # an old wheel_server.py centre, to be brought over
        cls.dev = evdev.g920()
        evdev.DEVICES.clear()
        evdev.DEVICES[cls.dev.path] = cls.dev
        cls.thread = threading.Thread(target=core.main, args=([],), daemon=True)
        cls.thread.start()
        for _ in range(100):
            if any(e[0] == "write" and e[1] == evdev.ecodes.FF_GAIN for e in cls.dev.log):
                break
            time.sleep(0.05)
        time.sleep(0.2)

    def uploads(self, etype):
        return [e for e in self.dev.log if e[0] == "upload" and e[1] == etype]

    def test_1_idle_spring_and_old_centre(self):
        self.assertTrue(self.uploads(evdev.ecodes.FF_SPRING), "the idle centring spring plays")
        self.assertAlmostEqual(S.Settings().center, (33000 - 32768) / 32768.0, places=4)
        self.assertFalse(os.path.exists(os.path.join(HOME, ".wheel_center")))
        S.write("center", "0.0")
        time.sleep(1.3)

    def test_2_commands(self):
        self.assertIn("no game connected", command(self.port, "status"))
        self.assertIn("logitech-g920", command(self.port, "info"))
        self.assertIn("vibrating", command(self.port, "buzz 0.8"))
        time.sleep(0.2)
        self.assertTrue(self.uploads(evdev.ecodes.FF_PERIODIC), "buzz vibrates")

    def test_3_warning_vibration(self):
        self.dev.log.clear()
        self.dev.inject((evdev.ecodes.EV_ABS, evdev.ecodes.ABS_X, 32768 + int(420 / 450.0 * 32767)))
        time.sleep(2.3)                       # (after the buzz from test 2 ends)
        self.assertTrue(self.uploads(evdev.ecodes.FF_PERIODIC), "turning near the end stop vibrates")
        self.dev.inject((evdev.ecodes.EV_ABS, evdev.ecodes.ABS_X, 32768))
        time.sleep(0.2)
        self.assertIn(("erase",), [e[:1] for e in self.dev.log])

    def test_4_game(self):
        S.write("center", "0.0")
        time.sleep(1.2)
        g = Game(self.port)
        g.send(P.T_HELLO, b"\x01")
        caps, rng, name = P.ACK.unpack(g.recv(P.T_HELLO_ACK)[:P.ACK.size])
        self.assertEqual(rng, 360)
        self.assertTrue(caps & P.CAP_FFB and caps & P.CAP_SHIFTER)
        self.assertIn(b"G920", name)
        e = evdev.ecodes
        self.dev.inject((e.EV_ABS, e.ABS_X, 32768 + int(90 / 450.0 * 32767)),   # 90 degrees right
                        (e.EV_ABS, e.ABS_Y, 0),                                 # throttle floored
                        (e.EV_KEY, 288, 1), (e.EV_KEY, 300, 1),                 # A, gear 1
                        (e.EV_ABS, e.ABS_HAT0X, -1))
        time.sleep(0.2)
        st = g.latest_state()
        _ms, steer, gas, brake, clutch, buttons, hx, hy, gear, _ = st
        self.assertAlmostEqual(steer, 0.5, places=2)
        self.assertAlmostEqual(gas, 1.0, places=2)
        self.assertAlmostEqual(brake, 0.0, places=2)
        self.assertTrue(buttons & 1)
        self.assertEqual(gear, 1)
        self.assertEqual(hx, -1)
        self.assertIn("game connected", command(self.port, "status"))
        # the game's force: positive pushes right -> the kernel level is negative (G920 convention)
        self.dev.log.clear()
        g.send(P.T_FFB, P.FFB.pack(80, 0, 0, 0.3, 0, 0, 0, 0, P.FLAG_WHEEL_FORCE, 9000, 12000))
        time.sleep(0.2)
        consts = self.uploads(e.FF_CONSTANT)
        self.assertTrue(consts, self.dev.log)
        self.assertLess(consts[-1][3]["ff_constant_effect"][1], 0)
        # settings apply at once
        S.write("gain", "0.3")
        time.sleep(1.3)
        self.assertIn(("write", e.FF_GAIN, int(0.3 * 0xFFFF)), self.dev.log)
        S.write("gain", "0.6")
        g.send(P.T_BYE)
        time.sleep(0.3)
        self.assertIn("no game connected", command(self.port, "status"))


class TestGenericWheel(unittest.TestCase):
    def test_wheel_without_profile(self):
        from netxwheel import backend_evdev
        d = evdev.g920("/dev/input/event9")
        d.name = "Acme Racing Wheel Pro"
        evdev.DEVICES.clear()
        evdev.DEVICES[d.path] = d
        w, prof = backend_evdev.open_wheel(S.Settings(), profiles.load_all())
        self.assertEqual(prof.id, "generic")
        self.assertTrue(w.has_ffb)
        self.assertEqual(len(w.keys), 18)
        self.assertFalse(w.has_shifter)      # generic: no gears from random buttons
        d.inject((evdev.ecodes.EV_KEY, 288, 1))
        w.poll()
        self.assertEqual(w.state.buttons & 1, 1)
        self.assertAlmostEqual(w.state.throttle, 0.0)        # rest found as high


if __name__ == "__main__":
    unittest.main(verbosity=2)
