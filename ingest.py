# ingest.py
"""Same as `cards ingest`; kept so old notes and habits still work."""
import sys

from creditcard.__main__ import main

if __name__ == "__main__":
    sys.exit(main(["ingest", *sys.argv[1:]]))
