#!/usr/bin/env python3
"""
cmodel_hex.py - load a C-model intermediate-node hex dump into a comparable
series of hex values, so it can be diffed against the RTL waveform (VCD).

Pure stdlib. The C "phyUD" dump format is INTERNAL to your project, so this
file is an *adapter*: it auto-detects a few common layouts, and there is one
clearly-marked place to plug in your exact format if auto-detect is wrong.

Supported auto-detected layouts (per line, '#' and '//' comments ignored):
  1. one hex per line                 ->  "A5"            (index = line order)
  2. "<idx> <hex>"  two columns       ->  "0 A5"
  3. "<node> = <hex>"  (use --node)   ->  "phyUD = A5"
  4. "<node> <idx> <hex>" (use --node)->  "phyUD 0 A5"

CLI:
  python3 cmodel_hex.py dump.hex [--node phyUD] [--limit N]
      -> prints "idx<TAB>hex" lines (the canonical series other tools consume)

Library:
  series = load_series("dump.hex", node="phyUD")   # -> ["A5", "00", ...]
"""
import re
import sys

_HEX = re.compile(r"^[0-9a-fA-F]+$")


def _clean(line):
    line = line.split("#", 1)[0].split("//", 1)[0].strip()
    return line


def load_series(path, node=None, limit=None):
    """Return a list of hex strings (upper-case, no 0x), in cycle/index order."""
    series = []
    with open(path, "r", errors="replace") as f:
        for raw in f:
            line = _clean(raw)
            if not line:
                continue
            parts = line.replace("=", " ").split()
            if node is not None:
                # keep only lines mentioning this node; hex is the last token
                if node not in parts:
                    continue
                hx = parts[-1]
            else:
                # layout 1: single token ; layout 2: "<idx> <hex>"
                if len(parts) == 1:
                    hx = parts[0]
                elif len(parts) == 2 and parts[0].lstrip("-").isdigit():
                    hx = parts[1]
                else:
                    hx = parts[-1]
            hx = hx[2:] if hx.lower().startswith("0x") else hx
            if not _HEX.match(hx):
                continue
            series.append(hx.upper().lstrip("0") or "0")
            if limit and len(series) >= limit:
                break
    return series

    # ----------------------------------------------------------------------
    # IF AUTO-DETECT IS WRONG FOR YOUR phyUD FORMAT, replace the loop above
    # with explicit parsing, e.g.:
    #     m = re.match(r"cycle=(\d+)\s+phyUD=([0-9a-f]+)", line)
    #     if m: series.append(m.group(2).upper())
    # Keep the return type a list[str] of hex (no 0x, no leading zeros) so
    # compare.py keeps working unchanged.
    # ----------------------------------------------------------------------


def _main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    node = argv[argv.index("--node") + 1] if "--node" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    for i, hx in enumerate(load_series(path, node, limit)):
        print("%d\t%s" % (i, hx))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
