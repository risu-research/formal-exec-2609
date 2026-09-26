#!/usr/bin/env bash
set -u
OUT=.runner/out
rm -rf "$OUT"
mkdir -p "$OUT"
BASE=0d1fae88f5f6191fe249baecf404e5b846f7e116
HEAD=3e5ed81a48339acd79fb4e620219d81a099078ef
UP=Consensys-Incorporated/evm-dafny
WORK=/tmp/r03
rm -rf "$WORK"
mkdir -p "$WORK"

# Historical verifier used by upstream CI.
curl -L --retry 3 -o "$WORK/dafny.zip" \
  https://github.com/dafny-lang/dafny/releases/download/v3.7.3/dafny-3.7.3-x64-ubuntu-16.04.zip
unzip -q "$WORK/dafny.zip" -d "$WORK/dafny373"
DAFNY=$(find "$WORK/dafny373" -type f -name dafny | head -1)
chmod +x "$DAFNY"
"$DAFNY" /version > "$OUT/verifier.txt" 2>&1 || true

for pair in base:$BASE head:$HEAD; do
  name=${pair%%:*}; sha=${pair#*:}
  curl -L --retry 3 -o "$WORK/$name.tar.gz" "https://codeload.github.com/$UP/tar.gz/$sha"
  mkdir -p "$WORK/$name"
  tar -xzf "$WORK/$name.tar.gz" -C "$WORK/$name" --strip-components=1
  printf '%s,%s\n' "$name" "$sha" >> "$OUT/historical_refs.csv"
done

# Preserve exact upstream file hashes.
sha256sum "$WORK/base/src/dafny/state.dfy" "$WORK/head/src/dafny/state.dfy" > "$OUT/state_sha256.txt"

# Exact endpoints plus four fixed-head causal hybrids.
cp -a "$WORK/base" "$WORK/H0"
cp -a "$WORK/head" "$WORK/H1"
for c in C00 C10 C01 C11; do cp -a "$WORK/head" "$WORK/$c"; done

python3 - "$WORK" <<'PY'
from pathlib import Path
import sys
w=Path(sys.argv[1])
p=w/'head/src/dafny/state.dfy'
t=p.read_text()
start=t.index("        function method Expand(address: nat, len: nat): (s': State)")
end=t.index("        /**\n         *  Get the size of the memory.", start)
new_block=t[start:end]
old_block="""        function method Expand(address: nat, len: nat) : State
        requires !IsFailure() {
            OK(evm.(memory:=Memory.Expand(evm.memory,address,len)))
        }

"""
old_contract_new_body="""        function method Expand(address: nat, len: nat) : State
        requires !IsFailure() {
            OK(evm.(memory:=Memory.Expand2(evm.memory, address + len - 1)))
        }

"""
new_contract_old_body=new_block.replace(
    "OK(evm.(memory:=Memory.Expand2(evm.memory, address + len - 1)))",
    "OK(evm.(memory:=Memory.Expand(evm.memory,address,len)))")
blocks={'C00':old_block,'C10':old_contract_new_body,'C01':new_contract_old_body,'C11':new_block}
for name,block in blocks.items():
    q=w/name/'src/dafny/state.dfy'
    s=q.read_text()
    a=s.index("        function method Expand(address: nat, len: nat): (s': State)")
    b=s.index("        /**\n         *  Get the size of the memory.", a)
    q.write_text(s[:a]+block+s[b:])
    (w/f'{name}.Expand.txt').write_text(block)
PY

for c in C00 C10 C01 C11; do
  cp "$WORK/$c.Expand.txt" "$OUT/$c.Expand.txt"
done

printf 'case,exit_code,verified,errors\n' > "$OUT/results.csv"
verify_case() {
  c=$1
  d="$WORK/$c"
  set +e
  (cd "$d" && "$DAFNY" /compile:0 /verifyAllModules src/dafny/evm.dfy src/dafny/evms/berlin.dfy) > "$OUT/$c.log" 2>&1
  ec=$?
  set -e
  # Last verifier summary if available.
  summary=$(grep -E 'Dafny program verifier finished|verified, [0-9]+ error' "$OUT/$c.log" | tail -1 || true)
  verified=$(printf '%s' "$summary" | sed -nE 's/.*finished with ([0-9]+) verified.*/\1/p')
  errors=$(printf '%s' "$summary" | sed -nE 's/.*verified, ([0-9]+) error.*/\1/p')
  printf '%s,%s,%s,%s\n' "$c" "$ec" "${verified:-NA}" "${errors:-NA}" >> "$OUT/results.csv"
}
set -e
for c in H0 H1 C00 C10 C01 C11; do verify_case "$c"; done

# Causal-source diffs and manifest.
diff -u "$OUT/C00.Expand.txt" "$OUT/C10.Expand.txt" > "$OUT/body_delta.diff" || true
diff -u "$OUT/C00.Expand.txt" "$OUT/C01.Expand.txt" > "$OUT/contract_plus_body_context.diff" || true
cat > "$OUT/MANIFEST.md" <<EOF
# r03 verifier replay

Historical source: $UP PR 171
Base: $BASE
Head: $HEAD
Historical verifier: Dafny 3.7.3 (upstream CI version)

H0/H1 are exact historical trees. C00/C10/C01/C11 all use the exact head tree except for the State.Expand function block. C00=old contract+old body; C10=old contract+new body; C01=new contract+old body; C11=new contract+new body. C11 is byte-for-byte the head State.Expand block. The four causal cells are counterfactual isolation states, not claimed historical commits.

No outcome criterion was used to select this candidate; it was selected from a frozen systematic screening frame before replay.
EOF
find "$OUT" -maxdepth 1 -type f -print0 | sort -z | xargs -0 sha256sum > "$OUT/SHA256SUMS.txt"
cat "$OUT/results.csv"
exit 0
