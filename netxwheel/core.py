"""The server: talks to the game (PROTOCOL.md) and drives the wheel through a backend.

A backend (backend_evdev.py on Linux, backend_sdl.py on Windows) reads one wheel and plays forces
on it. Everything here is the same for every wheel and every system.
"""
import os
import select
import socket
import sys
import time

from . import __version__, profiles, protocol as P, settings as S

STATE_HZ = 100          # wheel state packets per second while a game is connected
FFB_TIMEOUT = 0.30      # seconds without a force packet before the wheel goes limp
CLIENT_TIMEOUT = 3.0    # seconds without any packet before the game is forgotten
FFB_MAX_HZ = 60         # most force updates per second sent to the wheel


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Steering:
    """Turns the wheel's position into the game's steering, and the idle mode's forces."""

    def __init__(self, cfg, profile):
        self.cfg = cfg
        self.rotation = cfg.wheel_rotation or profile.rotation or 900
        self.steer_degrees = int(clamp(cfg.steer_degrees, 30, self.rotation))

    def wheel_pos(self, raw):
        """-1..1 of the wheel's own rotation, after the saved centre."""
        return clamp(raw - self.cfg.center, -1.0, 1.0)

    def game(self, raw):
        """-1..1 for the game: full lock at steer_degrees / 2 each way, held beyond."""
        return clamp(self.wheel_pos(raw) * self.rotation / float(self.steer_degrees), -1.0, 1.0)

    def idle(self, raw):
        """Idle spring (0..1) and warning vibration (0..1, of the wheel's strongest)."""
        c = self.cfg
        p = abs(self.wheel_pos(raw))
        if p < c.warn_start or p >= c.warn_stop:
            vib = 0.0
        else:
            t = clamp((p - c.warn_start) / max(0.001, 0.99 - c.warn_start), 0.0, 1.0)
            vib = c.warn_soft + t * (c.warn_strong - c.warn_soft)
        return clamp(c.idle_spring, 0.0, 1.0), clamp(vib, 0.0, 1.0)

    def describe(self, raw):
        pos = self.wheel_pos(raw)
        half = self.rotation / 2.0
        side = "right" if pos > 0.005 else "left" if pos < -0.005 else ""
        return "wheel %.0f%% %s(%.0f of %.0f degrees), game steering %.0f%%" % (
            abs(pos) * 100, side + " " if side else "", abs(pos) * half, half, abs(self.game(raw)) * 100)


class Server:
    def __init__(self, backend, sock, cfg, profile):
        self.b = backend
        self.sock = sock
        self.cfg = cfg
        self.profile = profile
        self.steer = Steering(cfg, profile)
        self.cfg_mtime = S.mtime()
        self.client = None
        self.last_rx = self.last_ffb = self.last_force = 0.0
        self.ffb_active = False
        self.seq = 0
        self.t0 = time.monotonic()
        self.buzz_until, self.buzz_level = 0.0, 0.0
        self.pending = None

    # ------------------------------------------------------------ helpers
    def send(self, addr, typ, payload):
        self.seq = (self.seq + 1) & 0xFFFF
        try:
            self.sock.sendto(P.header(typ, self.seq) + payload, addr)
        except OSError:
            pass

    def forces(self, spring, push, vib, now):
        """spring 0..1, push -1..1 (positive turns the wheel right), vib 0..1; limited to FFB_MAX_HZ."""
        if now - self.last_force < 1.0 / FFB_MAX_HZ:
            self.pending = (spring, push, vib)   # applied a moment later (tick), so none is lost
            return
        self.pending = None
        self.last_force = now
        if self.cfg.invert_force:
            push = -push
        self.b.set_forces(clamp(spring, 0.0, 1.0), self.cfg.center, clamp(push, -1.0, 1.0),
                          clamp(vib, 0.0, 1.0))

    def stop_forces(self):
        self.ffb_active = False
        self.pending = None
        self.b.stop_forces()

    def reload_settings(self):
        m = S.mtime()
        if m == self.cfg_mtime:
            return
        self.cfg_mtime = m
        old = self.cfg
        self.cfg = S.Settings()
        self.steer = Steering(self.cfg, self.profile)
        if self.cfg.gain != old.gain:
            self.b.set_gain(self.cfg.gain)
        if self.cfg.port != old.port:
            log("the port changes after a restart (netxwheel restart)")
        if self.cfg.profile != old.profile:
            log("the profile changes after a restart (netxwheel restart)")
        log("settings reloaded")
        self.b.refresh_forces()

    # ------------------------------------------------------------ local commands
    def command(self, text):
        words = text.split()
        cmd = words[0] if words else ""
        raw = self.b.state.steer
        if cmd == "status":
            game = "game connected from %s" % self.client[0] if self.client else (
                "no game connected (idle forces %s)" % ("on" if self.cfg.idle_forces else "off"))
            return "%s | %s | %s (profile %s) | centre %s" % (
                self.steer.describe(raw), game, self.b.name, self.profile.id,
                "the wheel's own" if abs(self.cfg.center) < 1e-6 else "%.4f" % self.cfg.center)
        if cmd == "center":
            S.write("center", "%.5f" % clamp(raw, -0.5, 0.5))
            self.reload_settings()
            log("new centre saved")
            return "centre saved: this position is now straight ahead"
        if cmd == "reset":
            S.write("center", "0.0")
            self.reload_settings()
            log("centre reset")
            return "centre reset to the wheel's own"
        if cmd == "buzz":
            if self.client:
                return "a game is connected: buzz works only while no game is connected"
            try:
                level = clamp(float(words[1]), 0.05, 1.0) if len(words) > 1 else 0.5
            except ValueError:
                level = 0.5
            self.buzz_until, self.buzz_level = time.monotonic() + 2.0, level
            log("test vibration, strength", level)
            return "vibrating for 2 seconds at strength %.2f" % level
        if cmd == "info":
            return "NetXWheel %s | wheel: %s | %s | profile %s (%s) | rotation %d, game full lock %d" % (
                __version__, self.b.device_name, self.b.name, self.profile.id,
                self.profile.file or "built in", self.steer.rotation, self.steer.steer_degrees)
        return "unknown command %r" % text

    # ------------------------------------------------------------ packets
    def packet(self, data, addr, now):
        if data[:4] == P.CMD_MAGIC:
            if addr[0] in ("127.0.0.1", "::1"):
                reply = self.command(data[4:].decode(errors="ignore").strip())
                try:
                    self.sock.sendto(reply.encode()[:1400], addr)
                except OSError:
                    pass
            return
        if len(data) < P.HDR.size:
            return
        magic, ver, typ, _seq = P.HDR.unpack_from(data)
        if magic != P.MAGIC:
            return
        body = data[P.HDR.size:]
        if typ == P.T_HELLO:
            if self.client != addr:
                log("game connected from", addr[0], "(its protocol version %d)" % ver)
                self.stop_forces()           # the idle forces end; the game's take over
            self.client = addr
            self.last_rx = now
            caps = (P.CAP_FFB if self.b.has_ffb else 0) | (P.CAP_SHIFTER if self.b.has_shifter else 0)
            name = self.b.device_name.encode("utf-8", "replace")[:32]
            self.send(addr, P.T_HELLO_ACK, P.ACK.pack(caps, self.steer.steer_degrees, name))
        elif typ == P.T_BYE and addr == self.client:
            log("game disconnected")
            self.client = None
            self.stop_forces()
        elif typ == P.T_FFB and addr == self.client and len(body) >= P.FFB.size:
            (_speed, _brake, abs_l, _steer_f, jolt, traction, _under, _over,
             flags, spring, const) = P.FFB.unpack_from(body)
            self.last_rx = self.last_ffb = now
            self.ffb_active = True
            if flags & P.FLAG_WHEEL_FORCE:
                vib = max(abs_l * 0.5, traction * 0.3, jolt) * self.cfg.game_vibration
                self.forces(spring / 32767.0, const / 32767.0, vib, now)
            else:
                self.forces(0.0, 0.0, 0.0, now)

    # ------------------------------------------------------------ main loop
    def tick(self, now):
        if self.client and now - self.last_rx > CLIENT_TIMEOUT:
            log("game stopped answering, forgetting it")
            self.client = None
            self.stop_forces()
        if self.ffb_active and now - self.last_ffb > FFB_TIMEOUT:
            self.stop_forces()
        if self.pending and now - self.last_force >= 1.0 / FFB_MAX_HZ:
            self.forces(*self.pending, now)
        st = self.b.state
        if self.client is None and self.cfg.idle_forces:
            spring, vib = self.steer.idle(st.steer)
            if now < self.buzz_until:
                vib = self.buzz_level
            self.forces(spring, 0.0, vib, now)
        elif self.client is None and now < self.buzz_until:
            self.forces(0.0, 0.0, self.buzz_level, now)
        if self.client:
            self.send(self.client, P.T_STATE, P.STATE.pack(
                int((now - self.t0) * 1000) & 0xFFFFFFFF,
                self.steer.game(st.steer), st.throttle, st.brake, st.clutch,
                st.buttons & 0xFFFFFFFF, st.hat_x, st.hat_y, st.gear, 0))

    def run(self):
        self.b.set_gain(self.cfg.gain)
        log("wheel ready: %s (profile %s, %s) - listening on UDP port %d" % (
            self.b.device_name, self.profile.id, self.b.name, self.cfg.port))
        next_tick = time.monotonic()
        last_cfg_check = 0.0
        fd = self.b.fileno()
        try:
            while True:
                now = time.monotonic()
                wait = max(0.0, next_tick - now)
                watch = [self.sock] + ([fd] if fd is not None else [])
                r, _, _ = select.select(watch, [], [], wait)
                if fd is None or fd in r:
                    self.b.poll()
                if self.sock in r:
                    while True:
                        try:
                            data, addr = self.sock.recvfrom(1500)
                        except (BlockingIOError, ConnectionResetError):
                            break
                        self.packet(data, addr, time.monotonic())
                now = time.monotonic()
                if now - last_cfg_check > 1.0:
                    last_cfg_check = now
                    self.reload_settings()
                if now >= next_tick:
                    next_tick += 1.0 / STATE_HZ
                    if next_tick < now:
                        next_tick = now + 1.0 / STATE_HZ
                    self.tick(now)
        finally:
            try:
                self.b.stop_forces()
            except OSError:
                pass


def pick_backend(name=None):
    """The backend module for this system: evdev on Linux, SDL elsewhere (or as asked)."""
    name = name or os.environ.get("NETXWHEEL_BACKEND") or ("evdev" if sys.platform.startswith("linux") else "sdl")
    if name == "evdev":
        from . import backend_evdev as mod
    elif name == "sdl":
        from . import backend_sdl as mod
    else:
        raise SystemExit("unknown backend %r (use evdev or sdl)" % name)
    return mod


def migrate_old_center():
    """The old wheel_server.py kept the centre in ~/.wheel_center (raw 0..65535): bring it over once."""
    old = os.path.expanduser("~/.wheel_center")
    if not os.path.exists(old):
        return
    try:
        with open(old) as f:
            raw = int(f.read().strip())
        if abs(S.Settings().center) < 1e-9:
            S.write("center", "%.5f" % clamp((raw - 32768) / 32768.0, -0.5, 0.5))
            log("brought over the centre saved by the old wheel_server.py")
        os.replace(old, old + ".old")
    except (OSError, ValueError):
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    backend_name = None
    if "--backend" in argv:
        i = argv.index("--backend")
        backend_name = argv[i + 1] if i + 1 < len(argv) else None
    S.ensure()
    migrate_old_center()
    cfg = S.Settings()
    mod = pick_backend(backend_name)
    log("NetXWheel %s, settings: %s" % (__version__, S.path()))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", cfg.port))
    except OSError as e:
        log("cannot use UDP port %d (%s): is another wheel server already running?" % (cfg.port, e))
        log("  stop it first (Ctrl+C in its window, or: netxwheel stop), then start this one again")
        return 1
    sock.setblocking(False)
    said_waiting = False
    while True:
        cfg = S.Settings()
        all_profiles = profiles.load_all()
        try:
            backend, profile = mod.open_wheel(cfg, all_profiles)
        except KeyboardInterrupt:
            return 0
        if backend is None:
            if not said_waiting:
                log("no wheel found yet (plug it in and switch it on); still looking...")
                said_waiting = True
            time.sleep(3)
            continue
        said_waiting = False
        try:
            Server(backend, sock, cfg, profile).run()
        except OSError as e:
            log("lost the wheel:", e)
            time.sleep(2)
        except KeyboardInterrupt:
            log("stopped")
            return 0
        finally:
            try:
                backend.close()
            except Exception:  # noqa: BLE001 - closing a vanished device may fail any way
                pass
