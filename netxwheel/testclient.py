"""Pretends to be a game: shows the wheel as a game sees it, and can play test forces.

  netxwheel test                  the server on this machine
  netxwheel test 192.168.1.50     a server elsewhere
  netxwheel test ffb              also play gentle test forces for 15 seconds
"""
import math
import socket
import time

from . import protocol as P, settings as S


def main(args):
    play = "ffb" in args
    hosts = [a for a in args if a != "ffb"]
    host = hosts[0] if hosts else "127.0.0.1"
    addr = (host, S.Settings().port if host == "127.0.0.1" else P.DEFAULT_PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    seq = [0]

    def send(typ, payload=b""):
        seq[0] += 1
        try:
            sock.sendto(P.header(typ, seq[0]) + payload, addr)
        except OSError:
            pass

    start = time.time()
    last_hello = last_print = 0.0
    st = None
    connected = False
    print("Connecting to %s:%d as a game - Ctrl+C to stop" % addr)
    try:
        while True:
            now = time.time()
            if now - last_hello > 1.0:
                send(P.T_HELLO, b"\x01")
                last_hello = now
            try:
                while True:
                    data, _ = sock.recvfrom(1500)
                    if len(data) < P.HDR.size or data[:4] != P.MAGIC:
                        continue
                    _m, ver, typ, _s = P.HDR.unpack_from(data)
                    body = data[P.HDR.size:]
                    if typ == P.T_HELLO_ACK and not connected:
                        caps, rng, name = P.ACK.unpack_from(body)
                        connected = True
                        print("Connected: %s, protocol %d, full lock at %d degrees, force feedback %s, shifter %s" % (
                            name.split(b"\0")[0].decode(errors="replace"), ver, rng,
                            "yes" if caps & P.CAP_FFB else "no", "yes" if caps & P.CAP_SHIFTER else "no"))
                    elif typ == P.T_STATE and len(body) >= P.STATE.size:
                        st = P.STATE.unpack_from(body)
            except (BlockingIOError, ConnectionResetError):
                pass
            if play and st is not None and now - start < 15:
                push = int(6000 * math.sin((now - start) * math.pi))      # positive = right
                send(P.T_FFB, P.FFB.pack(80.0, 0, 0, 0, 0, 0, 0, 0, P.FLAG_WHEEL_FORCE, 8000, push))
            if st is not None and now - last_print > 0.1:
                last_print = now
                _ms, steer, gas, brake, clutch, buttons, hx, hy, gear, _ = st
                pressed = [n for n, b in P.BUTTON_BIT.items() if buttons & (1 << b) and not n.startswith("gear")
                           and n != "reverse"]
                gear_s = "R" if gear < 0 else ("N" if gear == 0 else str(gear))
                line = "steer %+5.2f  gas %.2f  brake %.2f  clutch %.2f  gear %s  hat %+d %+d  %s" % (
                    steer, gas, brake, clutch, gear_s, hx, hy, " ".join(pressed))
                print("\r" + line[:150].ljust(150), end="", flush=True)
            if not connected and now - start > 5 and int(now - start) % 5 == 0 and now - last_print > 1:
                last_print = now
                print("still no answer: is the server running at %s?" % host)
            time.sleep(0.01)
    except KeyboardInterrupt:
        print()
    finally:
        send(P.T_BYE)
    return 0
