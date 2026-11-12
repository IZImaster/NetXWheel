"""A NetXWheel client in Python: what a game does, in one small file (PROTOCOL.md).

    from netxwheel.client import discover, Connection
    servers = discover()                         # [(address, announce), ...]
    c = Connection(servers[0][0], wheel=1, server_password="", wheel_password="")
    c.connect()                                  # raises ConnectError with the server's reason
    while True:
        state = c.poll()                         # the newest STATE (dict) or None
        c.send_forces(on=True, constant=0.2, spring=0.3)
"""
import socket
import time

from . import protocol as P


class ConnectError(Exception):
    def __init__(self, reason, message, extra=None):
        super().__init__("%s: %s" % (reason, message))
        self.reason, self.message, self.extra = reason, message, extra or {}


def discover(addresses=("255.255.255.255",), port=P.DEFAULT_PORT, seconds=1.0, client="NetXWheel test client"):
    """Every server that answers within some seconds: a list of ((ip, port), announce dict)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.1)
    body = {"proto": P.PROTO, "client": client}
    found = {}
    end = time.time() + seconds
    next_ask = 0.0
    try:
        while time.time() < end:
            if time.time() >= next_ask:
                next_ask = time.time() + 0.5
                for a in addresses:
                    try:
                        sock.sendto(P.pack(P.DISCOVER, body), (a, port))
                    except OSError:
                        pass
            try:
                data, addr = sock.recvfrom(4096)
            except (socket.timeout, ConnectionResetError):
                continue
            p = P.parse(data)
            if p and p.type == P.ANNOUNCE and not p.signed:
                j = p.json()
                if j and j.get("proto") == P.PROTO:
                    found[j.get("server_id") or addr] = (addr, j)
    finally:
        sock.close()
    return list(found.values())


class Connection:
    def __init__(self, address, wheel=1, server_password="", wheel_password="", client="NetXWheel test client",
                 state_hz=100):
        self.addr = (address[0], address[1]) if isinstance(address, tuple) else (address, P.DEFAULT_PORT)
        self.wheel_index = wheel
        self.server_password, self.wheel_password = server_password, wheel_password
        self.client, self.state_hz = client, state_hz
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.2)
        self.session = 0
        self.key = None
        self.seq_out = self.seq_in = 0
        self.wheel = None              # the wheel description from ACCEPT
        self.notices = []
        self.closed = False
        self.last_rx = time.monotonic()

    # ------------------------------------------------------------ connecting
    def _wait(self, cn, types, seconds=2.0):
        """The next setup answer for our client_nonce (anything else is ignored, as the spec says)."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                data, addr = self.sock.recvfrom(4096)
            except (socket.timeout, ConnectionResetError):
                continue
            if addr != self.addr:
                continue
            p = P.parse(data)
            if p is None or p.type not in types:
                continue
            if p.type == P.ACCEPT:
                return p, None
            j = p.json()
            if j and j.get("client_nonce") == cn:
                return p, j
        raise ConnectError("timeout", "no answer from %s:%d" % self.addr)

    def connect(self):
        cn = P.nonce()
        hello = {"proto": P.PROTO, "wheel": self.wheel_index, "client": self.client, "client_nonce": cn}
        self.sock.sendto(P.pack(P.CONNECT, hello), self.addr)
        p, j = self._wait(cn, (P.CHALLENGE, P.REJECT))
        if p.type == P.REJECT:
            raise ConnectError(j.get("reason"), j.get("message"), j)
        self.challenge = j
        key = P.derive_key(self.server_password, self.wheel_password, j["salt"], int(j.get("iterations", P.ITERATIONS)))
        sk = P.session_key(key, j["nonce"], cn)
        proof = dict(hello, nonce=j["nonce"], proof=P.proof(key, j["nonce"], cn, self.wheel_index),
                     state_hz=self.state_hz)
        self.sock.sendto(P.pack(P.CONNECT, proof), self.addr)
        while True:
            p, j = self._wait(cn, (P.ACCEPT, P.REJECT))
            if p.type == P.REJECT:
                raise ConnectError(j.get("reason"), j.get("message"), j)
            if P.verify(p, sk):          # a real ACCEPT is signed with our session key
                break                    # (a fake one isn't: keep waiting)
        a = p.json()
        self.session, self.key, self.seq_in = a["session"], sk, p.seq
        self.wheel = a["wheel"]
        self.accept = a
        self.last_rx = time.monotonic()
        return a

    # ------------------------------------------------------------ the session
    def _send(self, typ, body=b""):
        self.seq_out += 1
        self.sock.sendto(P.pack(typ, body, self.session, self.seq_out, self.key), self.addr)

    def send_forces(self, on=True, constant=0.0, spring=0.0, spring_center=0.0, damper=0.0, friction=0.0,
                    rumble=0.0, rumble_hz=0.0):
        self._send(P.FORCES, P.pack_forces(on, constant, spring, spring_center, damper, friction, rumble, rumble_hz))

    def keepalive(self):
        self._send(P.KEEPALIVE)

    def bye(self):
        if self.key and not self.closed:
            self._send(P.BYE)
        self.closed = True

    def poll(self, seconds=0.0):
        """Reads what arrived; returns the newest STATE (dict) or None. NOTICEs go to .notices."""
        newest = None
        end = time.time() + seconds
        self.sock.settimeout(max(0.001, seconds) if seconds else 0.001)
        first = True
        while first or time.time() < end:
            first = False
            try:
                data, addr = self.sock.recvfrom(4096)
            except (socket.timeout, BlockingIOError, ConnectionResetError):
                continue
            first = True                     # keep reading what's already there, but not past the time
            if time.time() >= end and seconds:
                first = False
            p = P.parse(data)
            if (p is None or addr != self.addr or p.session != self.session or not P.verify(p, self.key)
                    or p.seq <= self.seq_in):
                continue
            self.seq_in = p.seq
            self.last_rx = time.monotonic()
            if p.type == P.STATE:
                newest = P.unpack_state(p.body)
            elif p.type == P.NOTICE:
                self.notices.append(p.json() or {})
            elif p.type == P.BYE:
                self.closed = True
        return newest
