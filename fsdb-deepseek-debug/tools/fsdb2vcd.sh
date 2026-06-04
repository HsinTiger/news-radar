#!/bin/sh
# fsdb2vcd.sh - thin wrapper around Verdi's fsdb2vcd converter.
#
# fsdb2vcd is a Verdi (Synopsys) utility, NOT a vcs command and NOT the Verdi
# GUI. It runs directly in the shell. Requires a Verdi install on PATH.
#
# Usage:  ./fsdb2vcd.sh input.fsdb [output.vcd]
# Default output: <input>.vcd
#
# If you only need VCD for inspection, you usually do NOT need this at all:
# add `$dumpfile("dump.vcd"); $dumpvars(0, tb);` to the testbench and compile
# with `vcs -debug_access+all` to get VCD natively (no Verdi license needed).

set -e

IN="$1"
OUT="${2:-${IN%.fsdb}.vcd}"

if [ -z "$IN" ]; then
    echo "usage: $0 input.fsdb [output.vcd]" >&2
    exit 2
fi

# Prefer fsdb2vcd64 for large files if present.
BIN="$(command -v fsdb2vcd64 || command -v fsdb2vcd || true)"
if [ -z "$BIN" ]; then
    echo "ERROR: fsdb2vcd not found on PATH." >&2
    echo "       Set up Verdi first, e.g.:" >&2
    echo "         setenv VERDI_HOME /tools/synopsys/verdi/<ver>" >&2
    echo "         set path = (\$VERDI_HOME/bin \$path)" >&2
    echo "       (or 'module load verdi' per your CAD environment)" >&2
    exit 127
fi

echo "[fsdb2vcd] $BIN $IN -o $OUT" >&2
"$BIN" "$IN" -o "$OUT"
echo "[fsdb2vcd] wrote $OUT" >&2
