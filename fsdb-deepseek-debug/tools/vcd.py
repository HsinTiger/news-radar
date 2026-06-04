#!/usr/bin/env python3
"""
vcd.py - self-contained VCD (IEEE 1364) parser + query CLI.

Pure standard library only (no pip) -> safe for air-gapped deployment.
Purpose: give an LLM agent DETERMINISTIC answers about a waveform so it
never has to "eyeball" raw VCD and hallucinate signal names / values.

CLI:
    python3 vcd.py list     dump.vcd [--match clk]
    python3 vcd.py value    dump.vcd <signal> <time>
    python3 vcd.py changes  dump.vcd <signal> [t0] [t1]
    python3 vcd.py wavejson dump.vcd --clk <clk> --sig a b c [--t0 N] [--t1 N]

Library:
    w = VCD("dump.vcd")
    w.signals()                      -> ["tb.dut.clk", ...]
    w.value_at("tb.dut.data", 150)   -> "10100101" | "1"/"0"/"x"/"z" | None
    w.changes("tb.dut.ack", 0, 200)  -> [(t, val), ...]
    w.posedges("tb.dut.clk")         -> [t, ...]
    w.to_wavejson(clk, [sigs], t0, t1)
"""
import sys
import json
from bisect import bisect_right


class VCD:
    def __init__(self, path):
        self.timescale = None
        self.id2width = {}      # symbol id -> bit width
        self.name2id = {}       # name alias -> id (full/base/leaf, with & w/o bitrange)
        self.canonical = []     # canonical full names (with bitrange), declaration order
        self._changes = {}      # id -> sorted list of (time, value_str)
        self.end_time = 0
        self._parse_header(path)
        self._parse_values(path)

    # ---------- header ----------
    def _parse_header(self, path):
        scope = []
        with open(path, "r", errors="replace") as f:
            toks = self._tokens(f)
            for tok in toks:
                if tok == "$scope":
                    next(toks)                       # scope type
                    scope.append(next(toks))         # scope name
                    self._skip(toks)
                elif tok == "$upscope":
                    if scope:
                        scope.pop()
                    self._skip(toks)
                elif tok == "$var":
                    next(toks)                       # var type
                    width = int(next(toks))
                    vid = next(toks)
                    ref = next(toks)
                    nxt = next(toks)
                    bitrange = ""
                    if nxt != "$end":                # optional "[7:0]"
                        bitrange = nxt
                        self._skip(toks)
                    base = ".".join(scope + [ref])      # no bitrange
                    full = base + bitrange              # canonical
                    self.id2width[vid] = width
                    self.canonical.append(full)
                    # register aliases: full, base, leaf+bitrange, leaf
                    for alias in (full, base, ref + bitrange, ref):
                        self.name2id.setdefault(alias, vid)
                elif tok == "$timescale":
                    self.timescale = next(toks)
                    self._skip(toks)
                elif tok == "$enddefinitions":
                    self._skip(toks)
                    return
                elif tok in ("$date", "$version", "$comment"):
                    self._skip(toks)

    # ---------- value changes ----------
    def _parse_values(self, path):
        # Token-based: VCD allows several scalar changes on one line
        # ("0! 1\" 0#"), and vector/real changes span two tokens ("b1010 !").
        t = 0
        with open(path, "r", errors="replace") as f:
            toks = self._tokens(f)
            # skip the definition section
            for tok in toks:
                if tok == "$enddefinitions":
                    self._skip(toks)
                    break
            for tok in toks:
                c = tok[0]
                if c == "#":
                    try:
                        t = int(tok[1:])
                        self.end_time = max(self.end_time, t)
                    except ValueError:
                        pass
                elif c in "01xzXZ":                  # scalar: "1!"
                    self._changes.setdefault(tok[1:], []).append((t, c.lower()))
                elif c in "bBrR":                     # vector/real: value + next id
                    val = tok[1:]
                    try:
                        vid = next(toks)
                    except StopIteration:
                        break
                    self._changes.setdefault(vid, []).append((t, val))
                elif tok == "$comment":               # skip commentary
                    self._skip(toks)
                # $dumpvars/$dumpon/$dumpoff/$dumpall/$end fall through harmlessly

    # ---------- helpers ----------
    @staticmethod
    def _tokens(f):
        for line in f:
            for tok in line.split():
                yield tok

    @staticmethod
    def _skip(toks):
        for tok in toks:
            if tok == "$end":
                return

    def _resolve(self, name):
        if name in self.name2id:
            return self.name2id[name]
        cands = [n for n in self.name2id if n.endswith("." + name)]
        uniq = sorted(set(self.name2id[c] for c in cands))
        if len(uniq) == 1:
            return uniq[0]
        if len(uniq) > 1:
            raise KeyError("ambiguous signal '%s' -> %s" % (name, cands[:8]))
        raise KeyError("signal not found: %s" % name)

    # ---------- queries ----------
    def signals(self, match=None):
        full = sorted(set(self.canonical))
        return [n for n in full if (match in n)] if match else full

    def width(self, name):
        return self.id2width.get(self._resolve(name), 1)

    def value_at(self, name, time):
        ch = self._changes.get(self._resolve(name), [])
        if not ch:
            return None
        i = bisect_right([c[0] for c in ch], time) - 1
        return ch[i][1] if i >= 0 else None

    def changes(self, name, t0=0, t1=None):
        ch = self._changes.get(self._resolve(name), [])
        if t1 is None:
            t1 = self.end_time
        return [(t, v) for (t, v) in ch if t0 <= t <= t1]

    def posedges(self, clk, t0=0, t1=None):
        ch = self._changes.get(self._resolve(clk), [])
        if t1 is None:
            t1 = self.end_time
        edges, prev = [], None
        for (t, v) in ch:
            if v == "1" and prev in ("0", "x", "z", None) and t0 <= t <= t1:
                edges.append(t)
            prev = v
        return edges

    def to_wavejson(self, clk, sigs, t0=0, t1=None, hexfmt=True):
        edges = self.posedges(clk, t0, t1)
        n = len(edges)
        out = [{"name": clk, "wave": ("p" + "." * (n - 1)) if n else ""}]
        for s in sigs:
            w1 = self.width(s) == 1
            wave, data, prev = "", [], object()
            for e in edges:
                v = self.value_at(s, e)
                if w1:
                    cur = v if v in ("0", "1", "x", "z") else "x"
                    wave += cur if cur != prev else "."
                    prev = cur
                else:
                    disp = self._fmt_vec(v, hexfmt)
                    if disp != prev:
                        wave += "="
                        data.append(disp)
                        prev = disp
                    else:
                        wave += "."
            lane = {"name": s, "wave": wave}
            if data:
                lane["data"] = data
            out.append(lane)
        return {"signal": out}

    @staticmethod
    def _fmt_vec(v, hexfmt):
        if v is None:
            return "x"
        if any(ch in "xz" for ch in v):
            return v
        if hexfmt:
            try:
                return format(int(v, 2), "X")
            except ValueError:
                return v
        return v


def _main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd, path = argv[1], argv[2]
    w = VCD(path)
    try:
        return _dispatch(w, cmd, argv)
    except KeyError as e:
        # clean error (no traceback) so the agent learns it used a bad name
        sys.stderr.write("ERROR: %s\n" % str(e).strip('"'))
        return 2


def _dispatch(w, cmd, argv):
    if cmd == "list":
        match = argv[argv.index("--match") + 1] if "--match" in argv else None
        print("\n".join(w.signals(match)))
    elif cmd == "value":
        v = w.value_at(argv[3], int(argv[4]))
        print("not_found" if v is None else v)
    elif cmd == "changes":
        t0 = int(argv[4]) if len(argv) > 4 else 0
        t1 = int(argv[5]) if len(argv) > 5 else None
        for (t, v) in w.changes(argv[3], t0, t1):
            print("%d\t%s" % (t, v))
    elif cmd == "wavejson":
        clk = argv[argv.index("--clk") + 1]
        i, sigs = argv.index("--sig") + 1, []
        while i < len(argv) and not argv[i].startswith("--"):
            sigs.append(argv[i]); i += 1
        t0 = int(argv[argv.index("--t0") + 1]) if "--t0" in argv else 0
        t1 = int(argv[argv.index("--t1") + 1]) if "--t1" in argv else None
        print(json.dumps(w.to_wavejson(clk, sigs, t0, t1), indent=2))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
