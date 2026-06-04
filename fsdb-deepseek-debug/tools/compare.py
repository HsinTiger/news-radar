#!/usr/bin/env python3
"""
compare.py - DETERMINISTICALLY find the first divergence between the RTL
waveform (VCD) and the C-model golden hex dump for one node.

This is the anti-hallucination core of the debug loop: the *tool* decides
where RTL diverges from golden; the LLM only explains WHY and proposes a fix.

It samples the VCD signal at each clk posedge, converts to hex, and compares
that series element-by-element against the C-model series.

CLI:
  python3 compare.py dump.vcd --clk clk --sig tb.dut.phyUD \\
          golden.hex [--node phyUD] [--t0 N] [--t1 N] [--skip-x]

  --skip-x  drop leading reset/x samples so the golden series (which usually
            starts at the first valid output) aligns to the first real value.

Exit code: 0 = match, 3 = mismatch found, 2 = usage/lookup error.
"""
import sys
import json

from vcd import VCD
from cmodel_hex import load_series


def _bits_to_hex(v):
    if v is None:
        return "x"
    if any(c in "xz" for c in v):
        return v
    try:
        return format(int(v, 2), "X").lstrip("0") or "0"
    except ValueError:
        return v


def sample_series(vcd, clk, sig, t0=0, t1=None):
    """Sample sig at every clk posedge -> [(time, hex), ...]."""
    out = []
    for e in vcd.posedges(clk, t0, t1):
        out.append((e, _bits_to_hex(vcd.value_at(sig, e))))
    return out


def compare(vcd_path, clk, sig, golden_path, node=None, t0=0, t1=None, skip_x=False):
    w = VCD(vcd_path)
    actual = sample_series(w, clk, sig, t0, t1)
    if skip_x:
        # drop leading reset/uninitialized samples so golden (which usually
        # starts at the first valid output) aligns to the first real value
        while actual and ("x" in actual[0][1] or "z" in actual[0][1]):
            actual.pop(0)
    golden = load_series(golden_path, node=node)
    n = min(len(actual), len(golden))
    result = {
        "signal": sig, "clk": clk,
        "actual_samples": len(actual), "golden_samples": len(golden),
        "compared": n, "match": True, "first_divergence": None,
    }
    for i in range(n):
        t, a = actual[i]                       # a is already normalized hex or x/z
        g = (golden[i].lstrip("0") or "0").upper()
        if a.upper() != g:
            result["match"] = False
            result["first_divergence"] = {
                "index": i, "clk_time": t, "golden": g, "actual": a.upper(),
            }
            break
    if result["match"] and len(actual) != len(golden):
        result["match"] = False
        result["first_divergence"] = {
            "index": n, "clk_time": None,
            "note": "length mismatch: actual=%d golden=%d" % (len(actual), len(golden)),
        }
    return result


def _main(argv):
    if "--clk" not in argv or "--sig" not in argv or len(argv) < 4:
        print(__doc__)
        return 2
    vcd_path = argv[1]
    clk = argv[argv.index("--clk") + 1]
    sig = argv[argv.index("--sig") + 1]
    node = argv[argv.index("--node") + 1] if "--node" in argv else None
    t0 = int(argv[argv.index("--t0") + 1]) if "--t0" in argv else 0
    t1 = int(argv[argv.index("--t1") + 1]) if "--t1" in argv else None
    skip_x = "--skip-x" in argv
    # golden path = first positional arg (after vcd_path) that is neither a
    # flag nor a flag's value
    flag_values = set()
    for flag in ("--clk", "--sig", "--node", "--t0", "--t1"):
        if flag in argv:
            flag_values.add(argv.index(flag) + 1)
    positionals = [a for i, a in enumerate(argv)
                   if i >= 2 and not a.startswith("--") and i not in flag_values]
    if not positionals:
        sys.stderr.write("ERROR: golden hex path missing\n")
        return 2
    golden_path = positionals[0]
    try:
        res = compare(vcd_path, clk, sig, golden_path, node, t0, t1, skip_x)
    except KeyError as e:
        sys.stderr.write("ERROR: %s\n" % str(e).strip('"'))
        return 2
    print(json.dumps(res, indent=2))
    return 0 if res["match"] else 3


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
