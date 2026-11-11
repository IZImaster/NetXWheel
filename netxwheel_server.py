#!/usr/bin/env python3
"""Starts the NetXWheel server. Usually it runs by itself (Linux: install.sh; Windows: windows/start.bat)."""
import sys

from netxwheel.core import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
