"""The server: finds wheels, talks to games (PROTOCOL.md) and drives the wheels through a backend.

A backend (backend_evdev.py on Linux, backend_sdl.py on Windows) reads wheels and plays forces on
them. Everything here is the same for every wheel and every system.
"""
import hmac
import json
import math
import os
import select
import socket
import sys
import time

from . import __version__, profiles, protocol as P, settings as S

SESSION_TIMEOUT = 3.0     # seconds without any packet from a game before its session ends
FORCE_TIMEOUT = 0.30      # seconds without FORCES ("on") before the wheel goes limp
CHALLENGE_TIMEOUT = 10.0  # seconds a CHALLENGE stays valid
FFB_MAX_HZ = 60           # most force updates per second sent to a wheel
SCAN_EVERY = 3.0          # seconds between looking for newly plugged wheels
IDLE_TICK = 0.01          # idle forces and timeouts are checked 100 times a second
DISCOVER_PER_SEC = 4      # answers per address per second
FAILS_PER_MIN = 5         # failed password attempts per address per minute


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def clamp(v, lo, hi):
    if not isinstance(v, (int, float)) or v != v or math.isinf(v):   # NaN, infinity, nonsense
        return lo if lo >= 0 else 0.0
    return lo if v < lo else hi if v > hi else v


def _state_file():
    return os.path.join(S.config_dir(), "server.json")


def load_identity():
    """This server's id and password salt: made once, kept across restarts."""
    p = _state_file()
    data = {}
    try:
        with open(p) as f:
            data = json.load(f)
    except (OSError, ValueError):
        pass
    changed = False
    for key, length in (("server_id", 16), ("salt", 32)):
        if not P.is_hex(data.get(key), length):
            data[key] = os.urandom(length // 2).hex()
            changed = True
    if changed:
        os.makedirs(S.config_dir(), exist_ok=True)
        with open(p, "w") as f:
            json.dump(data, f)
    return data["server_id"], data["salt"]


class Steering:
    """One wheel's position turned into the game's steering, and its idle forces."""

    def __init__(self, cfg, rotation, steer_degrees, center):
        self.cfg = cfg
        self.rotation = rotation or 900
        self.steer_degrees = int(clamp(steer_degrees or cfg.steer_degrees, 30, self.rotation))
        self.center = clamp(center, -0.5, 0.5)

    def wheel_pos(self, raw):
        """-1..1 of the wheel's own rotation, after its saved centre."""
        return clamp(raw - self.center, -1.0, 1.0)

    def game(self, raw):
        """-1..1 for the game: full lock at steer_degrees / 2 each way, held beyond."""
        return clamp(self.wheel_pos(raw) * self.rotation / float(self.steer_degrees), -1.0, 1.0)

    def game_to_wheel(self, g):
        """A position in the game's steering units (-1..1) in the wheel's own units (for spring_center)."""
        return clamp(self.center + g * self.steer_degrees / float(self.rotation), -1.0, 1.0)

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
        side = "right " if pos > 0.005 else "left " if pos < -0.005 else ""
        return "%.0f%% %s(%.0f of %.0f degrees), game steering %.0f%%" % (
            abs(pos) * 100, side, abs(pos) * half, half, abs(self.game(raw)) * 100)


class Session:
    def __init__(self, sid, addr, key, client, state_hz):
        self.id, self.addr, self.key, self.client = sid, addr, key, client
        self.seq_out = 0
        self.seq_in = 0
        self.t0 = self.last_rx = time.monotonic()
        self.forces = None            # the last FORCES (dict)
        self.last_forces = 0.0
        self.state_hz = state_hz
        self.next_state = self.t0

    def name(self):
        return "%s (%s)" % (self.client or "a game", self.addr[0])


class Slot:
    """A wheel's place on this server: its number stays while the server runs, even when unplugged."""

    def __init__(self, index, wheel, cfg):
        self.index = index
        self.uid = wheel.uid
        self.wheel = wheel
        self.profile = wheel.profile
        self.device_name = wheel.device_name
        self.desc = wheel.describe()
        self.session = None
        self.buzz_until = self.buzz_level = 0.0
        self.last_force = 0.0
        self.pending = None
        self.load(cfg)

    def load(self, cfg):
        self.wcfg = S.wheel(self.uid)
        try:
            center = float(self.wcfg.get("center") or 0.0)
        except ValueError:
            center = 0.0
        try:
            sd = int(float(self.wcfg.get("steer_degrees"))) if self.wcfg.get("steer_degrees") else None
        except ValueError:
            sd = None
        self.steer = Steering(cfg, self.profile.rotation, sd, center)

    def password(self):
        return self.wcfg.get("password", "")

    def raw(self):
        return self.wheel.state.steer if self.wheel else self.steer.center

    def vendor_model(self):
        p = self.profile
        if p.vendor or p.model:
            return p.vendor, p.model or p.name
        return "", self.device_name

    def info(self, full=False):
        vendor, model = self.vendor_model()
        d = {"index": self.index, "vendor": vendor, "model": model, "rotation": self.steer.rotation,
             "steer_range": self.steer.steer_degrees, "ffb": self.desc["ffb"],
             "direct_drive": self.profile.direct_drive, "pedals": self.desc["pedals"],
             "shifter": self.desc["shifter"], "password": bool(self.password()),
             "in_use": self.session is not None}
        if self.profile.torque_nm:
            d["torque_nm"] = self.profile.torque_nm
        if self.session:
            d["in_use_by"] = self.session.name()
        if self.wheel is None:
            d["connected"] = False
        if full:
            for k in ("forces", "buttons", "dpad", "extra_axes"):
                d[k] = self.desc[k]
        return d


class Server:
    def __init__(self, backend_mod, sock, cfg):
        self.mod = backend_mod
        self.sock = sock
        self.cfg = cfg
        self.server_id, self.salt = load_identity()
        self.cfg_mtime = S.mtime()
        self.slots = []
        self.sessions = {}            # session id -> slot
        self.challenges = {}          # server nonce -> {client_nonce, wheel, addr, t}
        self.discover_log = {}        # ip -> [times]
        self.fail_log = {}            # ip -> [times]
        self.key_cache = {}
        self.last_scan = -SCAN_EVERY
        self.said_waiting = False
        self.all_profiles = profiles.load_all()

    # ------------------------------------------------------------ wheels
    def scan(self):
        open_uids = {s.uid for s in self.slots if s.wheel}
        try:
            new = self.mod.open_new(self.cfg, self.all_profiles, open_uids)
        except OSError as e:
            log("could not look for wheels:", e)
            return
        for w in new:
            slot = next((s for s in self.slots if s.uid == w.uid), None)
            if slot:
                slot.wheel, slot.desc = w, w.describe()
                log("wheel %d back: %s" % (slot.index, w.device_name))
                if slot.session:
                    self.notice(slot, {"event": "plugged"})
            else:
                first = not self.slots
                slot = Slot(len(self.slots) + 1, w, self.cfg)
                self.slots.append(slot)
                if first and abs(self.cfg.old_center) > 1e-9 and S.wheel(w.uid) == S.WHEEL_KEYS:
                    S.write_wheel(w.uid, "center", "%.5f" % self.cfg.old_center, w.device_name)
                    slot.load(self.cfg)
                log("wheel %d: %s (profile %s)" % (slot.index, w.device_name, slot.profile.id))
            w.set_gain(self.cfg.gain)
            self.said_waiting = False
        if not any(s.wheel for s in self.slots) and not self.said_waiting:
            log("no wheel found yet (plug one in and switch it on); still looking...")
            self.said_waiting = True

    def unplug(self, slot, why):
        log("wheel %d lost (%s)" % (slot.index, why))
        try:
            slot.wheel.close()
        except Exception:  # noqa: BLE001 - a vanished device may fail any way
            pass
        slot.wheel = None
        if slot.session:
            self.notice(slot, {"event": "unplugged"})

    # ------------------------------------------------------------ forces
    def forces(self, slot, now, constant=0.0, spring=0.0, center=None, damper=0.0, friction=0.0,
               rumble=0.0, period_ms=40):
        if slot.wheel is None:
            return
        args = (constant, spring, center, damper, friction, rumble, period_ms)
        if now - slot.last_force < 1.0 / FFB_MAX_HZ:
            slot.pending = args           # applied a moment later, so none is lost
            return
        slot.pending = None
        slot.last_force = now
        if self.cfg.invert_force:
            constant = -constant
        slot.wheel.set_forces(clamp(constant, -1.0, 1.0), clamp(spring, 0.0, 1.0),
                              slot.steer.center if center is None else clamp(center, -1.0, 1.0),
                              clamp(damper, 0.0, 1.0), clamp(friction, 0.0, 1.0), clamp(rumble, 0.0, 1.0),
                              int(clamp(period_ms, 4, 1000)))

    def game_forces(self, slot, f, now):
        if not f or not f["on"]:
            self.forces(slot, now)
            return
        hz = clamp(f["rumble_hz"], 0.0, 250.0) or 25.0
        self.forces(slot, now, constant=f["constant"], spring=f["spring"],
                    center=slot.steer.game_to_wheel(clamp(f["spring_center"], -1.0, 1.0)),
                    damper=f["damper"], friction=f["friction"],
                    rumble=clamp(f["rumble"], 0.0, 1.0) * clamp(self.cfg.rumble_strength, 0.0, 1.0),
                    period_ms=1000.0 / max(1.0, hz))

    def stop_forces(self, slot):
        slot.pending = None
        if slot.wheel:
            try:
                slot.wheel.stop_forces()
            except OSError:
                pass

    # ------------------------------------------------------------ sending
    def send(self, addr, typ, body=b""):
        try:
            self.sock.sendto(P.pack(typ, body), addr)
        except OSError:
            pass

    def send_session(self, slot, typ, body=b""):
        s = slot.session
        s.seq_out += 1
        try:
            self.sock.sendto(P.pack(typ, body, s.id, s.seq_out, s.key), s.addr)
        except OSError:
            pass

    def notice(self, slot, obj):
        if slot.session:
            self.send_session(slot, P.NOTICE, obj)

    def end_session(self, slot, why, notice=None):
        s = slot.session
        if not s:
            return
        if notice:
            self.notice(slot, notice)
            self.send_session(slot, P.BYE)
        log("wheel %d: %s left (%s)" % (slot.index, s.name(), why))
        self.sessions.pop(s.id, None)
        slot.session = None
        self.stop_forces(slot)

    def reject(self, addr, reason, message, client_nonce, **extra):
        body = {"reason": reason, "message": message, "client_nonce": client_nonce}
        body.update(extra)
        self.send(addr, P.REJECT, body)

    # ------------------------------------------------------------ the protocol
    @staticmethod
    def _recent(table, ip, window, now):
        times = [t for t in table.get(ip, []) if now - t < window]
        table[ip] = times
        return times

    def announce(self):
        return {"proto": P.PROTO, "server_id": self.server_id, "name": self.cfg.name,
                "software": "NetXWheel %s" % __version__, "password": bool(self.cfg.server_password),
                "wheels": [s.info() for s in self.slots]}

    def key_for(self, slot):
        k = (self.cfg.server_password, slot.password(), self.salt)
        if k not in self.key_cache:
            if len(self.key_cache) > 32:
                self.key_cache.clear()
            self.key_cache[k] = P.derive_key(k[0], k[1], self.salt)
        return self.key_cache[k]

    def on_connect(self, j, addr, now):
        cn = j.get("client_nonce")
        if not P.is_hex(cn, 32):
            return                                   # not even a proper CONNECT: no answer
        if j.get("proto") != P.PROTO:
            return self.reject(addr, "proto", "this server speaks protocol %d" % P.PROTO, cn, proto=P.PROTO)
        try:
            index = int(j.get("wheel"))
        except (TypeError, ValueError):
            index = 0
        slot = self.slots[index - 1] if 1 <= index <= len(self.slots) else None
        if slot is None or slot.wheel is None:
            return self.reject(addr, "no_wheel", "no wheel %s on this server right now" % j.get("wheel"), cn)
        if slot.session and slot.session.addr != addr:
            return self.reject(addr, "in_use", "wheel %d is being used by %s" % (index, slot.session.name()), cn)
        if "proof" not in j:                         # step 1: answer with a challenge
            for n in [n for n, c in self.challenges.items() if now - c["t"] > CHALLENGE_TIMEOUT]:
                del self.challenges[n]
            if len(self.challenges) > 256:
                return self.reject(addr, "busy", "too many attempts, try again soon", cn, retry_ms=2000)
            n = P.nonce()
            self.challenges[n] = {"client_nonce": cn, "wheel": index, "addr": addr, "t": now}
            return self.send(addr, P.CHALLENGE, {
                "nonce": n, "salt": self.salt, "iterations": P.ITERATIONS,
                "server_password": bool(self.cfg.server_password), "wheel_password": bool(slot.password()),
                "client_nonce": cn})
        # step 2: check the proof
        ip = addr[0]
        if len(self._recent(self.fail_log, ip, 60.0, now)) >= FAILS_PER_MIN:
            return self.reject(addr, "busy", "too many wrong passwords, wait a minute", cn, retry_ms=60000)
        n = j.get("nonce")
        c = self.challenges.pop(n, None) if isinstance(n, str) else None
        if (c is None or now - c["t"] > CHALLENGE_TIMEOUT or c["client_nonce"] != cn or c["wheel"] != index
                or c["addr"] != addr):
            return self.reject(addr, "nonce", "the challenge is old or unknown: start again", cn)
        key = self.key_for(slot)
        want = P.proof(key, n, cn, index)
        if not (isinstance(j.get("proof"), str) and hmac.compare_digest(want, j["proof"])):
            self.fail_log[ip].append(now)
            log("wheel %d: wrong password from %s" % (index, ip))
            return self.reject(addr, "password", "the password is wrong or missing", cn)
        if slot.session:                              # the same game reconnecting
            self.end_session(slot, "reconnected")
        sid = 0
        while sid == 0 or sid in self.sessions:
            sid = int.from_bytes(os.urandom(4), "little")
        try:
            hz = int(clamp(float(j.get("state_hz", 100)), 25, 250))
        except (TypeError, ValueError):
            hz = 100
        client = str(j.get("client") or "a game")[:64]
        s = Session(sid, addr, P.session_key(key, n, cn), client, hz)
        self.stop_forces(slot)                         # the idle forces end; the game's take over
        slot.session = s
        self.sessions[sid] = slot
        log("wheel %d: %s connected" % (index, s.name()))
        self.send_session(slot, P.ACCEPT, {"session": sid, "wheel": slot.info(full=True), "state_hz": hz,
                                           "timeout_ms": int(SESSION_TIMEOUT * 1000),
                                           "force_timeout_ms": int(FORCE_TIMEOUT * 1000)})

    def on_session_packet(self, p, addr, now):
        slot = self.sessions.get(p.session)
        if slot is None:
            return
        s = slot.session
        if addr != s.addr or not P.verify(p, s.key) or p.seq <= s.seq_in:
            return                                    # forged, from elsewhere, or old: ignored
        s.seq_in = p.seq
        s.last_rx = now
        if p.type == P.FORCES:
            f = P.unpack_forces(p.body)
            if f:
                s.forces = f
                if f["on"]:
                    s.last_forces = now
                self.game_forces(slot, f, now)
        elif p.type == P.KEEPALIVE:
            self.send_session(slot, P.KEEPALIVE)
        elif p.type == P.BYE:
            self.end_session(slot, "said bye")

    def packet(self, data, addr, now):
        if data[:4] == P.CMD_MAGIC:
            if addr[0] in ("127.0.0.1", "::1"):
                reply = self.command(data[4:].decode(errors="ignore").strip())
                try:
                    self.sock.sendto(reply.encode()[:8000], addr)
                except OSError:
                    pass
            return
        p = P.parse(data)
        if p is None:
            return
        if p.type == P.DISCOVER:
            recent = self._recent(self.discover_log, addr[0], 1.0, now)
            if len(recent) < DISCOVER_PER_SEC:
                recent.append(now)
                self.send(addr, P.ANNOUNCE, self.announce())
            if len(self.discover_log) > 1024:
                self.discover_log.clear()
        elif p.type == P.CONNECT:
            j = p.json()
            if j:
                self.on_connect(j, addr, now)
        elif p.type in (P.FORCES, P.KEEPALIVE, P.BYE):
            self.on_session_packet(p, addr, now)

    # ------------------------------------------------------------ settings
    def reload_settings(self):
        m = S.mtime()
        if m == self.cfg_mtime:
            return
        self.cfg_mtime = m
        old = self.cfg
        self.cfg = S.Settings()
        for slot in self.slots:
            before = slot.info(full=True)
            slot.load(self.cfg)
            if slot.wheel:
                if self.cfg.gain != old.gain:
                    slot.wheel.set_gain(self.cfg.gain)
                slot.wheel.refresh_forces()
            if slot.session and slot.info(full=True) != before:
                self.notice(slot, {"event": "changed", "wheel": slot.info(full=True)})
        if self.cfg.port != old.port:
            log("the port changes after a restart (netxwheel restart)")
        log("settings reloaded")

    # ------------------------------------------------------------ local commands
    def slot_arg(self, words, i=1):
        try:
            n = int(words[i]) if len(words) > i else 1
        except ValueError:
            return None
        return self.slots[n - 1] if 1 <= n <= len(self.slots) else None

    def command(self, text):
        words = text.split()
        cmd = words[0] if words else ""
        if cmd == "status":
            lines = ['"%s" - NetXWheel %s on UDP port %d%s' % (
                self.cfg.name, __version__, self.cfg.port, ", server password set" if self.cfg.server_password else "")]
            if not self.slots:
                lines.append("no wheel found yet (plug one in and switch it on)")
            for s in self.slots:
                use = ("used by %s" % s.session.name()) if s.session else (
                    "free (idle forces %s)" % ("on" if self.cfg.idle_forces else "off"))
                state = s.steer.describe(s.raw()) if s.wheel else "UNPLUGGED"
                lines.append("wheel %d: %s - %s - %s%s" % (s.index, s.device_name, state, use,
                                                          ", password" if s.password() else ""))
            return "\n".join(lines)
        if cmd == "info":
            out = [json.dumps(self.announce(), indent=1)]
            for s in self.slots:
                out.append("wheel %d: id %s, profile %s (%s), %s" % (
                    s.index, s.uid, s.profile.id, s.profile.file or "built in", self.mod.NAME))
            return "\n".join(out)
        if cmd in ("center", "reset", "buzz", "kick", "uid"):
            slot = self.slot_arg(words)
            if slot is None:
                return "no such wheel (netxwheel status lists them)"
            if cmd == "uid":
                return slot.uid + "\n" + slot.device_name
            if cmd == "kick":
                if not slot.session:
                    return "wheel %d isn't in use" % slot.index
                self.end_session(slot, "kicked", {"event": "kicked", "message": "the server's owner ended it"})
                return "wheel %d is free again" % slot.index
            if slot.wheel is None:
                return "wheel %d is unplugged" % slot.index
            if cmd == "center":
                S.write_wheel(slot.uid, "center", "%.5f" % clamp(slot.wheel.state.steer, -0.5, 0.5),
                              slot.device_name)
                self.reload_settings()
                return "wheel %d: centre saved, this position is now straight ahead" % slot.index
            if cmd == "reset":
                S.write_wheel(slot.uid, "center", "", slot.device_name)
                self.reload_settings()
                return "wheel %d: centre back to the wheel's own" % slot.index
            if slot.session:
                return "wheel %d is being used by a game: buzz works only on a free wheel" % slot.index
            try:
                level = clamp(float(words[2]), 0.05, 1.0) if len(words) > 2 else 0.5
            except ValueError:
                level = 0.5
            slot.buzz_until, slot.buzz_level = time.monotonic() + 2.0, level
            return "wheel %d: vibrating for 2 seconds at strength %.2f" % (slot.index, level)
        return "unknown command %r" % text

    # ------------------------------------------------------------ main loop
    def tick(self, now):
        for slot in self.slots:
            s = slot.session
            if s and now - s.last_rx > SESSION_TIMEOUT:
                self.end_session(slot, "stopped answering")
                s = None
            if s and s.forces and s.forces["on"] and now - s.last_forces > FORCE_TIMEOUT:
                s.forces = None
                self.stop_forces(slot)
            if slot.pending and slot.wheel and now - slot.last_force >= 1.0 / FFB_MAX_HZ:
                self.forces(slot, now, *slot.pending)
            if s is None and slot.wheel:
                spring, vib = slot.steer.idle(slot.wheel.state.steer) if self.cfg.idle_forces else (0.0, 0.0)
                if now < slot.buzz_until:
                    vib = slot.buzz_level
                self.forces(slot, now, spring=spring, rumble=vib)

    def send_states(self, now):
        for slot in self.slots:
            s = slot.session
            if not s or now < s.next_state:
                continue
            s.next_state += 1.0 / s.state_hz
            if s.next_state < now:
                s.next_state = now + 1.0 / s.state_hz
            ms = int((now - s.t0) * 1000)
            if slot.wheel:
                st = slot.wheel.state
                playing = bool(s.forces and s.forces["on"] and slot.desc["ffb"])
                body = P.pack_state(ms, slot.steer.game(st.steer), st.throttle, st.brake, st.clutch, st.handbrake,
                                    st.buttons, st.dpad, st.gear, P.STATUS_FORCES if playing else 0, st.extra)
            else:
                body = P.pack_state(ms, 0.0, 0, 0, 0, 0, 0, 0, 0, P.STATUS_UNPLUGGED,
                                    [0.0] * len(slot.desc["extra_axes"]))
            self.send_session(slot, P.STATE, body)

    def step(self, timeout_cap=None):
        """One round of the main loop."""
        now = time.monotonic()
        if now - self.last_scan >= SCAN_EVERY:
            self.last_scan = now
            self.scan()
        deadline = self.next_tick
        for slot in self.slots:
            if slot.session:
                deadline = min(deadline, slot.session.next_state)
        fds = {}
        polled = []
        for slot in self.slots:
            if slot.wheel:
                fd = slot.wheel.fileno()
                if fd is None:
                    polled.append(slot)
                else:
                    fds[fd] = slot
        wait = max(0.0, deadline - now)
        if timeout_cap is not None:
            wait = min(wait, timeout_cap)
        r, _, _ = select.select([self.sock] + list(fds), [], [], wait)
        for slot in polled + [fds[fd] for fd in r if fd in fds]:
            if slot.wheel:
                try:
                    slot.wheel.poll()
                except OSError as e:
                    self.unplug(slot, e)
        if self.sock in r:
            for _ in range(200):
                try:
                    data, addr = self.sock.recvfrom(2048)
                except (BlockingIOError, ConnectionResetError, InterruptedError):
                    break
                self.packet(data, addr, time.monotonic())
        now = time.monotonic()
        if now - self.last_cfg_check > 1.0:
            self.last_cfg_check = now
            self.reload_settings()
        if now >= self.next_tick:
            self.next_tick = max(self.next_tick + IDLE_TICK, now - IDLE_TICK)
            try:
                self.tick(now)
            except OSError as e:
                log("force feedback error:", e)
        self.send_states(now)

    def run(self):
        log('NetXWheel %s "%s", settings: %s - listening on UDP port %d' % (
            __version__, self.cfg.name, S.path(), self.cfg.port))
        self.next_tick = time.monotonic()
        self.last_cfg_check = 0.0
        try:
            while True:
                self.step()
        finally:
            self.shutdown()

    def shutdown(self):
        for slot in self.slots:
            if slot.session:
                self.end_session(slot, "server stopping", {"event": "shutdown"})
            if slot.wheel:
                try:
                    slot.wheel.close()
                except Exception:  # noqa: BLE001
                    pass
                slot.wheel = None


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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    backend_name = None
    if "--backend" in argv:
        i = argv.index("--backend")
        backend_name = argv[i + 1] if i + 1 < len(argv) else None
    S.ensure()
    cfg = S.Settings()
    mod = pick_backend(backend_name)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", cfg.port))
    except OSError as e:
        log("cannot use UDP port %d (%s): is another wheel server already running?" % (cfg.port, e))
        log("  stop it first (Ctrl+C in its window, or: netxwheel stop), then start this one again")
        return 1
    sock.setblocking(False)
    server = Server(mod, sock, cfg)
    try:
        server.run()
    except KeyboardInterrupt:
        log("stopped")
    return 0
