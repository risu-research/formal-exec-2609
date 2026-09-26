#!/usr/bin/env bash
set -euo pipefail
OUT="$(pwd)/.runner/out"; rm -rf "$OUT"; mkdir -p "$OUT"
WORK=/tmp/r20; rm -rf "$WORK"; mkdir -p "$WORK"
REL=v2026.04.17
curl -L --retry 3 -o "$WORK/fstar.tgz" "https://github.com/FStarLang/FStar/releases/download/${REL}/fstar-${REL}-Linux-x86_64.tar.gz"
tar -xzf "$WORK/fstar.tgz" -C "$WORK"
FSTAR=$(find "$WORK" -type f -name fstar.exe | head -1); chmod +x "$FSTAR"
"$FSTAR" --version > "$OUT/verifier.txt" 2>&1 || true
python3 - "$WORK" <<'PY'
from pathlib import Path
import sys
w=Path(sys.argv[1])
old_contract='''    : Pure (int_t n)\n      (requires (size (a / b) n))\n      (ensures (fun c -> b <> 0 ==> a / b = c))'''
new_contract='''    : Pure (int_t n)\n      (requires (size (a /- b) n))\n      (ensures (fun c -> b <> 0 ==> a /- b = c))'''
old_body='a / b'; new_body='a /- b'
for name,contract,body in [
 ('C00',old_contract,old_body),('C10',old_contract,new_body),
 ('C01',new_contract,old_body),('C11',new_contract,new_body)]:
 txt=f'''module Replay{name}\n\nopen FStar.Int\n\nlet div_replay (#n:pos) (a:int_t n) (b:int_t n{{b <> 0}})\n{contract}\n= {body}\n'''
 (w/f'{name}.fst').write_text(txt)
PY
printf 'case,exit_code,verified_marker,error_marker\n' > "$OUT/results.csv"
for c in C00 C10 C01 C11; do
  set +e
  "$FSTAR" "$WORK/$c.fst" > "$OUT/$c.log" 2>&1
  ec=$?
  set -e
  v=$(grep -c 'Verified module' "$OUT/$c.log" || true)
  e=$(grep -ciE 'error|failed' "$OUT/$c.log" || true)
  printf '%s,%s,%s,%s\n' "$c" "$ec" "$v" "$e" >> "$OUT/results.csv"
  cp "$WORK/$c.fst" "$OUT/$c.fst"
done
cat > "$OUT/MANIFEST.md" <<EOF
# r20 H055 minimal causal replay
Frozen before r18 full-source outcome. Official F* $REL compiler. Uses official FStar.Int int_t/size and exact old/new H055 contract/body expressions under fresh names. This is a causal litmus, not a historical source snapshot.
EOF
sha256sum "$OUT"/* > "$OUT/SHA256SUMS.txt" || true
cat "$OUT/results.csv"
