---
name: rtl-waveform-debug
description: >-
  Debug RTL/UVM verification failures and co-design timing behavior using
  waveforms. Use when the user points you at RTL or UVM source, a VCD waveform,
  a VCS simulation log, and/or a C-model intermediate-node hex dump (e.g.
  "phyUD") and asks you to find why a test fails, locate where RTL diverges
  from the golden model, explain a signal's timing, or generate/modify a
  WaveDrom timing diagram. Always query waveforms through the deterministic
  tools in this skill — never read raw VCD and guess signal values.
---

# RTL Waveform Debug & Co-Design

You help RTL designers in two loops. **Facts about waveforms always come from
the tools — never eyeball raw VCD/hex and assert values, or you will
hallucinate signal names and timing.** You reason and explain; the tools decide
what the values *are*.

## Inputs you will be given

- **RTL / UVM source** (file paths) — read directly; it is text.
- **VCD** — IEEE-1364 ASCII waveform. Query via `tools/vcd.py` (never paste raw).
  - If you are given an **FSDB** instead, convert first: `tools/fsdb2vcd.sh in.fsdb out.vcd` (needs Verdi on PATH).
- **VCS log** (e.g. `vcs.log`) — plain text. `grep` it for errors/`$error`/`UVM_ERROR`/`UVM_FATAL` with timestamps.
- **C-model hex dump** (intermediate node, e.g. `phyUD`) — the **golden reference**. Load via `tools/cmodel_hex.py`.

## Deterministic tools (in `tools/`, pure-Python stdlib, offline)

| Command | Use |
|---------|-----|
| `python3 tools/vcd.py list dump.vcd [--match X]` | enumerate real signal names (use to verify a name exists) |
| `python3 tools/vcd.py value dump.vcd <sig> <time>` | exact value of a signal at a time |
| `python3 tools/vcd.py changes dump.vcd <sig> [t0] [t1]` | all transitions of a signal in a window |
| `python3 tools/vcd.py wavejson dump.vcd --clk <clk> --sig a b c [--t0 N --t1 N]` | reconstruct WaveJSON sampled at clk posedges |
| `python3 tools/cmodel_hex.py golden.hex [--node phyUD]` | load golden series as `idx<TAB>hex` |
| `python3 tools/compare.py dump.vcd --clk <clk> --sig <node> golden.hex [--node phyUD] [--skip-x]` | **find the first divergence** RTL-vs-golden (rc 3 = mismatch) |
| `sh tools/render_wavedrom.sh wave.json5 out.svg` | render WaveJSON to SVG for the engineer |
| `sh tools/fsdb2vcd.sh in.fsdb out.vcd` | convert FSDB→VCD (Verdi) |

## Loop A — Debug a failing test (golden-driven)

1. **Read the log first.** `grep` `vcs.log` for the first error and its sim time `T` and the signal/scope it names.
2. **Confirm the signal exists.** `vcd.py list --match <name>`. If it's not in the list, you misread it — do not invent it.
3. **If there is a C-model golden for the node, let the tool find the divergence:**
   `python3 tools/compare.py dump.vcd --clk <clk> --sig <node> golden.hex --node <node> --skip-x`
   The tool returns the first `{index, clk_time, golden, actual}` mismatch. **You do not decide where they diverge — the tool does.**
4. **Inspect around the divergence.** `vcd.py changes <node> <t-Δ> <t+Δ>` and `vcd.py value` on related control/handshake signals at `clk_time`.
5. **Explain & fix.** Map the divergence back to the RTL: which always-block / assign / FSM state produced the wrong value, and why. Propose the RTL edit. Cite signal=value@time from tool output, never from memory.
6. **Re-verify after the fix** by re-running sim and re-running `compare.py` (expect rc 0).

## Loop B — Co-design via WaveDrom (intent alignment)

Use when writing/changing RTL with the designer, or when they describe desired
timing in plain language.

1. When you write/modify RTL, also emit a **WaveJSON** of its intended behavior and render it (`render_wavedrom.sh`) for the engineer.
2. When the engineer replies in plain language ("ack must rise the *same* cycle as req"), **translate it to WaveJSON, render it, and ask "is this what you mean?"** — this readback step catches misunderstandings before you touch RTL.
3. Once confirmed, treat that WaveJSON as the spec and edit the RTL to match it. Keep signal names / bit-widths consistent with the RTL.
4. To check the RTL actually matches intent, run sim → `vcd.py wavejson` on the same signals → compare against the intended WaveJSON.

## Hard anti-hallucination rules

- Every signal name you mention must appear in `vcd.py list`. If `value`/`compare` returns `ERROR: signal not found`, you used a wrong name — re-list, don't guess.
- Every "signal = value @ time" claim must come from `vcd.py value` / `compare.py` output in this session. No remembered values.
- Divergence location is whatever `compare.py` reports. Do not assert a different cycle from "reading" the waveform.
- When a tool returns `x`/`z`, say "unknown/uninitialized" — never round it to 0/1.
- For multi-bit `=` lanes in WaveJSON you author, the number of `=`/value slots must equal the length of the `data` array.

## Notes / limits

- The model is text-only — you cannot "look at" a waveform image. You generate WaveJSON (text); `render_wavedrom.sh` makes the picture for the human.
- `compare.py` aligns golden to actual by clk-posedge index; use `--skip-x` to drop leading reset/x samples, or `--t0` to start at the first valid cycle. If counts differ it reports a length mismatch — fix alignment before trusting per-index results.
- If the C-model dump format isn't auto-detected, edit the marked adapter block in `tools/cmodel_hex.py` (keep the return type `list[str]` of hex).
