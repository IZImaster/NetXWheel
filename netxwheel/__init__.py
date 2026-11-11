"""NetXWheel: a force feedback steering wheel over the network.

The server (netxwheel_server.py) runs next to the wheel, reads it and streams its state over UDP
to a game, and plays the game's force feedback back on the wheel. See PROTOCOL.md.
"""
__version__ = "0.1.0"  # prototype; 1.0 brings protocol version 1 (see the protocol-v2 branch)
