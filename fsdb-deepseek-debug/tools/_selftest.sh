#!/bin/sh
# _selftest.sh - smoke-test the deterministic tools after staging into the
# air-gapped network. Builds a tiny known VCD + golden and checks outputs.
# Exit 0 = all pass. Run from the package root: sh tools/_selftest.sh
set -e
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/dump.vcd" <<'EOF'
$timescale 1ns $end
$scope module tb $end
$scope module handshake $end
$var wire 1 ! clk $end
$var wire 1 " req $end
$var reg 1 # ack $end
$var reg 8 $ data [7:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
$dumpvars
0! 0" 0# bxxxxxxxx $
$end
#0
#5
1!
#10
0!
1"
#15
1!
1#
b10100101 $
#20
0!
#25
1!
#30
0!
EOF

printf 'A5\nA5\n' > "$TMP/golden_ok.hex"
printf 'A5\nFF\n' > "$TMP/golden_bad.hex"

fail=0
check() { # desc  expected  actual
    if [ "$2" = "$3" ]; then echo "PASS: $1"; else echo "FAIL: $1 (want '$2' got '$3')"; fail=1; fi
}

check "value data@15" "10100101" "$(python3 tools/vcd.py value "$TMP/dump.vcd" tb.handshake.data 15)"
check "value ack@12"  "0"        "$(python3 tools/vcd.py value "$TMP/dump.vcd" ack 12)"

# capture rc in a set -e-safe way (a failing command in `if` does not abort)
if python3 tools/vcd.py value "$TMP/dump.vcd" nosuchsig 10 2>/dev/null; then rc=0; else rc=$?; fi
check "missing signal rc" "2" "$rc"

if python3 tools/compare.py "$TMP/dump.vcd" --clk clk --sig data "$TMP/golden_ok.hex" --skip-x >/dev/null; then rc=0; else rc=$?; fi
check "compare match rc" "0" "$rc"

if python3 tools/compare.py "$TMP/dump.vcd" --clk clk --sig data "$TMP/golden_bad.hex" --skip-x >/dev/null; then rc=0; else rc=$?; fi
check "compare mismatch rc" "3" "$rc"

div=$(python3 tools/compare.py "$TMP/dump.vcd" --clk clk --sig data "$TMP/golden_bad.hex" --skip-x \
      | python3 -c "import sys,json;d=json.load(sys.stdin)['first_divergence'];print('%s,%s,%s'%(d['index'],d['golden'],d['actual']))")
check "divergence detail" "1,FF,A5" "$div"

if [ "$fail" = "0" ]; then echo "ALL PASS"; else echo "SOME FAILED"; fi
exit $fail
