"""Deprecated compatibility entry point.

The original file controlled Wuji Hand over USB.  It now delegates to the
Wuji Hand 2 network implementation so accidentally invoking the old filename
cannot use the legacy hardware API.
"""

from glove_to_hand import parse_args, run


if __name__ == "__main__":
    print("glove_to_hand_old.py is deprecated; using the Hand 2 backend.")
    run(parse_args())
