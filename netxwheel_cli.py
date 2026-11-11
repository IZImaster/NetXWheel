#!/usr/bin/env python3
"""The netxwheel command (install.sh makes it available as just: netxwheel). Try: netxwheel help"""
import sys

from netxwheel.cli import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
