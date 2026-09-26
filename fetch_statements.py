# fetch_statements.py
"""Same as `cards fetch`; kept so old notes and habits still work."""
import sys

from creditcard.__main__ import main

if __name__ == "__main__":
    sys.exit(main(["fetch", *sys.argv[1:]]))
