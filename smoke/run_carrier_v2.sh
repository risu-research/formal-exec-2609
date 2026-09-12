#!/usr/bin/env bash
set -uo pipefail

MODE=${1:?mode required: old-new|new-old|lock}
OUT=${2:?output directory required}
ROOT=$(pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

case "$MODE" in
  old-new)
    REV=128cab89f5edaae4699184f50c1334598ebe904e
    LABEL=old-carrier__new-address-contract
    ;;
  new-old|lock)
    REV=d511239e19be8fcc7f340a64554ea93699637e62
    LABEL=$([ "$MODE" = new-old ] && echo new-carrier__old-address-contract || echo new-carrier__removed-lock-realization)
    ;;
  *) echo "unknown mode $MODE" >&2; exit 2;;
esac

WORK="/tmp/proofscope-carrier-${MODE}-$$"
git clone -q https://github.com/ZipCPU/zipcpu.git "$WORK" || exit 3
cd "$WORK" || exit 3
git checkout -q "$REV" || exit 3
printf 'mode=%s\nrevision=%s\nlabel=%s\n' "$MODE" "$REV" "$LABEL" > "$OUT/meta.txt"
yosys -V > "$OUT/yosys.version.txt" 2>&1
z3 --version > "$OUT/z3.version.txt" 2>&1

# Force write_smt2 compatibility with the installed Yosys without changing semantics.
sed -i '/write_smt2/i dffunmap' bench/formal/pipemem.ys

build_model() {
  local tag=$1
  (cd bench/formal && yosys -ql "$OUT/${tag}.yslog" -s pipemem.ys) > "$OUT/${tag}.build.stdout.txt" 2> "$OUT/${tag}.build.stderr.txt"
  local rc=$?
  echo "$rc" > "$OUT/${tag}.build.exit"
  if [ "$rc" -eq 0 ]; then cp bench/formal/pipemem.smt2 "$OUT/${tag}.smt2"; fi
  return "$rc"
}

run_bmc_induction() {
  local tag=$1
  local smt="$OUT/${tag}.smt2"
  (cd bench/formal && yosys-smtbmc --presat -s z3 -t 40 --dump-vcd "$OUT/${tag}.bmc.vcd" "$smt") > "$OUT/${tag}.bmc.log" 2>&1
  local brc=$?; echo "$brc" > "$OUT/${tag}.bmc.exit"
  (cd bench/formal && yosys-smtbmc -s z3 -i -t 36 --dump-vcd "$OUT/${tag}.induction.vcd" "$smt") > "$OUT/${tag}.induction.log" 2>&1
  local irc=$?; echo "$irc" > "$OUT/${tag}.induction.exit"
  echo "$brc $irc"
}

# Same-toolchain native baseline. If it does not close, do not attribute a later failure to the foreign monitor.
if ! build_model baseline; then
  echo 'INSTRUMENTATION_FAILURE_BASELINE_BUILD' > "$OUT/outcome.txt"
  exit 0
fi
read BASE_BRC BASE_IRC < <(run_bmc_induction baseline)
printf 'baseline_bmc_exit=%s\nbaseline_induction_exit=%s\n' "$BASE_BRC" "$BASE_IRC" >> "$OUT/meta.txt"
if [ "$BASE_BRC" -ne 0 ]; then
  echo 'BASELINE_BMC_FAILURE' > "$OUT/outcome.txt"
  exit 0
fi
if [ "$BASE_IRC" -ne 0 ]; then
  echo 'BASELINE_INDUCTION_FAILURE' > "$OUT/outcome.txt"
  exit 0
fi

if [ "$MODE" = old-new ]; then
python3 - <<'PY'
from pathlib import Path
p=Path('rtl/core/pipemem.v'); s=p.read_text()
anchor="""\t\t\t`ASSUME(i_op[0] == o_wb_we);\n\t\tend\n"""
inject="""\t\t\t`ASSUME(i_op[0] == o_wb_we);\n\t\tend\n\n\t// PROOFSCOPE_CARRIER_V2 foreign monitor: R1 address contract on R0 native proof world\n\talways @(posedge i_clk)\n\t\tif ((f_past_valid)&&(f_cyc)&&(!i_wb_stall)&&(i_pipe_stb))\n\t\t\tassert((i_addr[(AW+1):2] == o_wb_addr)\n\t\t\t\t||(i_addr[(AW+1):2] == o_wb_addr+1));\n"""
if anchor not in s: raise SystemExit('old-new injection anchor not found')
p.write_text(s.replace(anchor,inject,1))
PY
elif [ "$MODE" = new-old ]; then
python3 - <<'PY'
from pathlib import Path
p=Path('rtl/core/pipemem.v'); s=p.read_text()
anchor="""\t\t\t`ASSUME(i_op[0] == o_wb_we);\n\t\tend\n"""
inject="""\t\t\t`ASSUME(i_op[0] == o_wb_we);\n\t\tend\n\n\t// PROOFSCOPE_CARRIER_V2 foreign monitor: R0 address contract on R1 native proof world\n\talways @(posedge i_clk)\n\t\tif ((f_past_valid)&&(f_cyc)&&(!i_wb_stall)&&(i_pipe_stb))\n\t\t\tassert((i_addr == o_wb_addr)||(i_addr == o_wb_addr+1));\n"""
if anchor not in s: raise SystemExit('new-old injection anchor not found')
p.write_text(s.replace(anchor,inject,1))
PY
else
python3 - <<'PY'
from pathlib import Path
p=Path('rtl/core/pipemem.v'); s=p.read_text()
anchor="""//always @(posedge i_clk)\n//\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n//\t\t`ASSUME(!i_lock);\n"""
probe=anchor+"""\n\t// PROOFSCOPE_CARRIER_V2 realization probe: behavior forbidden by removed R0 lock contract\n\talways @(posedge i_clk)\n\t\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock))&&(i_lock))\n\t\t\tcover(1'b1);\n"""
if anchor not in s: raise SystemExit('lock injection anchor not found')
p.write_text(s.replace(anchor,probe,1))
PY
fi
INJECT_RC=$?
echo "$INJECT_RC" > "$OUT/injection.exit"
if [ "$INJECT_RC" -ne 0 ]; then
  echo 'INSTRUMENTATION_FAILURE_INJECTION' > "$OUT/outcome.txt"
  exit 0
fi
cp rtl/core/pipemem.v "$OUT/instrumented-pipemem.v"

if ! build_model instrumented; then
  echo 'INSTRUMENTATION_FAILURE_BUILD' > "$OUT/outcome.txt"
  exit 0
fi

if [ "$MODE" = lock ]; then
  (cd bench/formal && yosys-smtbmc --presat -s z3 -c -t 40 --dump-vcd "$OUT/lock.cover.vcd" "$OUT/instrumented.smt2") > "$OUT/lock.cover.log" 2>&1
  CRC=$?; echo "$CRC" > "$OUT/lock.cover.exit"
  if grep -q 'Reached cover statement' "$OUT/lock.cover.log"; then
    echo 'REALIZED' > "$OUT/outcome.txt"
  elif grep -q 'Unreached cover statement' "$OUT/lock.cover.log"; then
    echo 'BOUNDED_NOT_OBSERVED' > "$OUT/outcome.txt"
  else
    echo 'INSTRUMENTATION_OR_SOLVER_FAILURE' > "$OUT/outcome.txt"
  fi
  exit 0
fi

read BRC IRC < <(run_bmc_induction instrumented)
printf 'instrumented_bmc_exit=%s\ninstrumented_induction_exit=%s\n' "$BRC" "$IRC" >> "$OUT/meta.txt"
if [ "$BRC" -ne 0 ]; then
  echo 'REALIZED_COUNTEREXAMPLE' > "$OUT/outcome.txt"
elif [ "$IRC" -eq 0 ]; then
  echo 'PROVED_BMC_AND_INDUCTION' > "$OUT/outcome.txt"
else
  echo 'INDUCTION_INCONCLUSIVE' > "$OUT/outcome.txt"
fi
