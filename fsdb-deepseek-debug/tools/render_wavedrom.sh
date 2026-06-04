#!/bin/sh
# render_wavedrom.sh - render a WaveJSON file to SVG using the wavedrom CLI.
#
# Usage:  ./render_wavedrom.sh wave.json5 [out.svg]
# Default output: <input>.svg
#
# Air-gapped note: vendor the wavedrom-cli npm package once on a connected
# machine (`npm pack wavedrom-cli`) and `npm i -g` the tarball inside the
# network, OR commit node_modules. `npx wavedrom-cli` works if it's installed.

set -e

IN="$1"
OUT="${2:-${IN%.*}.svg}"

if [ -z "$IN" ]; then
    echo "usage: $0 wave.json5 [out.svg]" >&2
    exit 2
fi

if command -v wavedrom-cli >/dev/null 2>&1; then
    wavedrom-cli --input "$IN" > "$OUT"
elif command -v wavedrom >/dev/null 2>&1; then
    wavedrom --input "$IN" > "$OUT"
elif command -v npx >/dev/null 2>&1; then
    npx --no-install wavedrom-cli --input "$IN" > "$OUT" 2>/dev/null \
        || npx wavedrom-cli --input "$IN" > "$OUT"
else
    echo "ERROR: no wavedrom renderer found (wavedrom-cli / wavedrom / npx)." >&2
    echo "       Install: npm i -g wavedrom-cli   (vendor the tarball if air-gapped)" >&2
    exit 127
fi

echo "[wavedrom] wrote $OUT" >&2
