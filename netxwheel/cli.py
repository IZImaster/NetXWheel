"""The netxwheel command. Run "netxwheel help" for the list."""
import os
import shutil
import socket
import subprocess
import sys
import time

from . import __version__, profiles, protocol as P, settings as S

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINUX = sys.platform.startswith("linux")
SERVICE = "netxwheel"

HELP = """NetXWheel %s - a steering wheel over the network

Wheel (the server must be running):
  netxwheel status            where the wheel is, whether a game is connected
  netxwheel watch             the same, live, until Ctrl+C
  netxwheel center            hold the wheel straight first: saves that as straight ahead
  netxwheel reset             back to the wheel's own centre
  netxwheel buzz [strength]   vibrate the wheel for 2 seconds (strength 0.05 .. 1.0, default 0.5)
  netxwheel info              which wheel, profile and rotation the server uses

Settings (they apply at once, no restart):
  netxwheel settings          show all settings and what they do
  netxwheel set NAME VALUE    change one, e.g.: netxwheel set gain 0.5

Wheels:
  netxwheel devices           the controllers this machine sees
  netxwheel profiles          the wheel profiles NetXWheel knows
  netxwheel make-profile      teach NetXWheel a new wheel (asks you to press things)
  netxwheel test [host] [ffb] pretend to be a game: show the wheel, and with ffb play test forces
%s"""

HELP_LINUX = """
The server (Linux, installed with install.sh):
  netxwheel start | stop | restart
  netxwheel log               the server's last messages (netxwheel log live: keep showing them)
  netxwheel update            get the newest NetXWheel and restart
"""

HELP_WINDOWS = """
The server (Windows): double-click windows\\start.bat (keep its window open while you play).
"""


def ask_server(text, timeout=1.0):
    port = S.Settings().port
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(P.CMD_MAGIC + text.encode(), ("127.0.0.1", port))
        return sock.recvfrom(2048)[0].decode(errors="replace")
    except (socket.timeout, ConnectionResetError, OSError):
        hint = "netxwheel start" if LINUX else "windows\\start.bat"
        return "no answer from the server: is it running? (start it with: %s)" % hint
    finally:
        sock.close()


def show_settings():
    S.ensure()
    print("Settings file:", S.path())
    print()
    values = S.read()
    for name, default, comment in S.DEFAULTS:
        mark = "" if values[name] == default else "   (default %s)" % default
        print("  %-15s %-8s %s%s" % (name, values[name], comment, mark))
    print()
    print("Change one with: netxwheel set NAME VALUE")


def set_setting(args):
    if len(args) != 2:
        print("usage: netxwheel set NAME VALUE   (netxwheel settings lists them)")
        return 2
    try:
        value = S.write(args[0], args[1])
    except ValueError as e:
        print("not changed:", e)
        return 2
    print("%s = %s (the server uses it within a second)" % (args[0], value))
    if args[0] in ("port", "profile"):
        print("this one needs a restart: netxwheel restart")
    return 0


def list_profiles():
    found = profiles.load_all()
    print("Profiles (%d):" % len(found))
    for p in found:
        where = "yours" if p.file and p.file.startswith(profiles.user_dir()) else "shipped"
        systems = "/".join(s for s in ("linux", "windows") if s in p.sections)
        print("  %-22s %-34s %-14s tested: %s (%s)" % (p.id, p.name, systems, p.tested, where))
    print("A wheel without a profile still works with generic settings (buttons in order).")
    print("Your own profiles: %s" % profiles.user_dir())


def list_devices():
    from .core import pick_backend
    mod = pick_backend()
    rows = mod.list_wheels()
    if not rows:
        print("No controllers found." + (" (Is your user in the 'input' group? Log out and in after install.)"
                                         if LINUX else ""))
        return
    allp = profiles.load_all()
    for path, name, wheel in rows:
        p = profiles.choose(name, "auto", allp)
        label = "profile %s" % p.id if p.id != "generic" else ("wheel, generic" if wheel else "")
        print("  %-22s %-44s %s" % (path, name, label))


def service(action):
    if not LINUX:
        print("On Windows start the server with windows\\start.bat.")
        return 1
    if not shutil.which("systemctl"):
        print("systemd is not available here; start the server with: python3 %s/netxwheel_server.py" % REPO)
        return 1
    return subprocess.call(["sudo", "systemctl", action, SERVICE])


def show_log(args):
    if not LINUX:
        print("On Windows the messages are in the start.bat window.")
        return 1
    cmd = ["journalctl", "-u", SERVICE, "--no-pager", "-o", "cat"]
    cmd += ["-f", "-n", "20"] if args[:1] == ["live"] else ["-n", "40"]
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


def update():
    if not os.path.isdir(os.path.join(REPO, ".git")):
        print("This copy was not installed with git; download the newest from GitHub instead.")
        return 1
    print("Getting the newest NetXWheel...")
    if subprocess.call(["git", "-C", REPO, "pull", "--ff-only"]) != 0:
        print("Could not update. If you changed files in %s, run: git -C %s stash   then try again." % (REPO, REPO))
        return 1
    if LINUX and shutil.which("systemctl"):
        print("Restarting the server...")
        return subprocess.call(["sudo", "systemctl", "restart", SERVICE])
    print("Done. Restart the server to use it.")
    return 0


def watch():
    try:
        while True:
            line = ask_server("status", 0.5)
            print("\r" + line[:150].ljust(150), end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "help"
    args = argv[1:]
    if cmd in ("status", "center", "reset", "info"):
        print(ask_server(cmd))
    elif cmd == "buzz":
        print(ask_server(" ".join(["buzz"] + args[:1])))
    elif cmd == "watch":
        return watch()
    elif cmd == "settings":
        show_settings()
    elif cmd == "set":
        return set_setting(args)
    elif cmd == "profiles":
        list_profiles()
    elif cmd == "devices":
        list_devices()
    elif cmd in ("make-profile", "makeprofile", "make_profile"):
        from . import make_profile
        return make_profile.main()
    elif cmd == "test":
        from . import testclient
        return testclient.main(args)
    elif cmd in ("start", "stop", "restart"):
        return service(cmd)
    elif cmd == "log":
        return show_log(args)
    elif cmd == "update":
        return update()
    elif cmd in ("version", "--version"):
        print("NetXWheel", __version__)
    elif cmd in ("help", "-h", "--help"):
        print(HELP % (__version__, HELP_LINUX if LINUX else HELP_WINDOWS))
    else:
        print("unknown command %r - see: netxwheel help" % cmd)
        return 2
    return 0
