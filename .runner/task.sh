#!/usr/bin/env bash
set -euo pipefail
BASE=31bb53ec1e93936eb3a72cda31a2d042c9cdbbdb
HEAD=bf26cf111684c01c8f53a6a5d7e07903fced5099
ROOT="$GITHUB_WORKSPACE/.runner/out"
IN="$ROOT/input"
MX="$ROOT/matrix"
RES="$ROOT/results"
mkdir -p "$IN" "$MX" "$RES" "$ROOT/diffs"

git init /tmp/u >/dev/null
git -C /tmp/u remote add origin https://github.com/haalfi/remote-store.git
git -C /tmp/u fetch --depth=1 origin "$BASE" "$HEAD" >/dev/null
for rev in base head; do
  if [ "$rev" = base ]; then sha="$BASE"; else sha="$HEAD"; fi
  git -C /tmp/u show "$sha:sdd/formal/BackendContract.dfy" > "$IN/${rev}-BackendContract.dfy"
  git -C /tmp/u show "$sha:sdd/formal/MemoryBackend.dfy" > "$IN/${rev}-MemoryBackend.dfy"
done
printf 'base=%s\nhead=%s\n' "$BASE" "$HEAD" > "$IN/refs.txt"
sha256sum "$IN"/*.dfy > "$IN/SHA256SUMS.txt"
dafny --version > "$ROOT/dafny-version.txt"

python3 - <<'PY'
from pathlib import Path
root = Path('.runner/out')
inp = root/'input'
base_b = (inp/'base-BackendContract.dfy').read_text()
head_b = (inp/'head-BackendContract.dfy').read_text()
base_m = (inp/'base-MemoryBackend.dfy').read_text()
head_m = (inp/'head-MemoryBackend.dfy').read_text()
new_contract = """      fs[dst].content == old(fs)[src].content &&\n      fs[dst].info.metadata == old(fs)[src].info.metadata\n"""
old_contract = """      fs[dst].content == old(fs)[src].content\n"""
if head_m.count(new_contract) != 2:
    raise SystemExit(f'unexpected strengthened-contract count: {head_m.count(new_contract)}')
b1s0_m = head_m.replace(new_contract, old_contract)
body_new = """    // BK-196 / WR-013: thread user metadata onto the copy destination.\n    // BasicFileInfo would drop it — the gap this item closes.\n    var newInfo := FileInfo(dst, dst, srcEntry.info.size,\n                            None, None, None, srcEntry.info.metadata);\n"""
body_old = """    var newInfo := BasicFileInfo(dst, dst, srcEntry.info.size);\n"""
if head_m.count(body_new) != 2:
    raise SystemExit(f'unexpected body-constructor count: {head_m.count(body_new)}')
b0s1_m = head_m.replace(body_new, body_old)
for a in ["      assert fs[dst].info.metadata == old(fs)[src].info.metadata;\n",
          "    assert fs[dst].info.metadata == old(fs)[src].info.metadata;\n"]:
    b0s1_m = b0s1_m.replace(a, '')
cases = {
    'B0S0': (base_b, base_m),
    'B1S0': (base_b, b1s0_m),
    'B0S1': (head_b, b0s1_m),
    'B1S1': (head_b, head_m),
}
for name, (bc, mb) in cases.items():
    d = root/'matrix'/name
    d.mkdir(parents=True, exist_ok=True)
    (d/'BackendContract.dfy').write_text(bc)
    (d/'MemoryBackend.dfy').write_text(mb)
assert (root/'matrix/B0S0/BackendContract.dfy').read_bytes() == (inp/'base-BackendContract.dfy').read_bytes()
assert (root/'matrix/B0S0/MemoryBackend.dfy').read_bytes() == (inp/'base-MemoryBackend.dfy').read_bytes()
assert (root/'matrix/B1S1/BackendContract.dfy').read_bytes() == (inp/'head-BackendContract.dfy').read_bytes()
assert (root/'matrix/B1S1/MemoryBackend.dfy').read_bytes() == (inp/'head-MemoryBackend.dfy').read_bytes()
PY

diff -u "$MX/B0S0/BackendContract.dfy" "$MX/B1S0/BackendContract.dfy" > "$ROOT/diffs/body-only-contract.diff" || true
diff -u "$MX/B0S0/MemoryBackend.dfy" "$MX/B1S0/MemoryBackend.dfy" > "$ROOT/diffs/body-only-memory.diff" || true
diff -u "$MX/B0S0/BackendContract.dfy" "$MX/B0S1/BackendContract.dfy" > "$ROOT/diffs/spec-only-contract.diff" || true
diff -u "$MX/B0S0/MemoryBackend.dfy" "$MX/B0S1/MemoryBackend.dfy" > "$ROOT/diffs/spec-only-memory.diff" || true

set +e
printf 'case,exit_code,status,verified,errors\n' > "$RES/results.csv"
for c in B0S0 B1S0 B0S1 B1S1; do
  (cd "$MX/$c" && dafny verify --verification-time-limit 120 MemoryBackend.dfy) > "$RES/$c.log" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then status=PASS; else status=FAIL; fi
  summary=$(grep -E 'Dafny program verifier finished with [0-9]+ verified, [0-9]+ error' "$RES/$c.log" | tail -1)
  verified=$(printf '%s' "$summary" | sed -nE 's/.*with ([0-9]+) verified, ([0-9]+) error.*/\1/p')
  errors=$(printf '%s' "$summary" | sed -nE 's/.*with ([0-9]+) verified, ([0-9]+) error.*/\2/p')
  printf '%s,%s,%s,%s,%s\n' "$c" "$rc" "$status" "${verified:-NA}" "${errors:-NA}" >> "$RES/results.csv"
done
set -e
find "$ROOT" -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum > "$ROOT/SHA256SUMS.txt"
cat "$RES/results.csv"
