#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out; rm -rf "$OUT"; mkdir -p "$OUT"
WORK=/tmp/r18; rm -rf "$WORK"; mkdir -p "$WORK"
PARENT=9d111f83fb03035701f6796004ec062a133e196a
HEAD=ff84ac08aba49743e5ec9f582799d0df62c5e6f4
RELEASE=v2026.04.17
URL="https://github.com/FStarLang/FStar/releases/download/${RELEASE}/fstar-${RELEASE}-Linux-x86_64.tar.gz"

curl -L --retry 3 -o "$WORK/fstar.tgz" "$URL"
tar -xzf "$WORK/fstar.tgz" -C "$WORK"
FSTAR=$(find "$WORK" -type f -name fstar.exe | head -1)
chmod +x "$FSTAR"
"$FSTAR" --version > "$OUT/verifier.txt" 2>&1 || true

git clone -q --filter=blob:none https://github.com/FStarLang/FStar.git "$WORK/up"
cd "$WORK/up"
git checkout -q "$HEAD"
mkdir -p "$WORK/head" "$WORK/parent"
git show "$HEAD:ulib/FStar.Int.fsti" > "$WORK/head/FStar.Int.fsti"
git show "$PARENT:ulib/FStar.Int.fsti" > "$WORK/parent/FStar.Int.fsti"
sha256sum "$WORK/head/FStar.Int.fsti" "$WORK/parent/FStar.Int.fsti" > "$OUT/source_sha256.txt"

python3 - "$WORK" <<'PY'
from pathlib import Path
import sys,re
w=Path(sys.argv[1])
old=(w/'parent/FStar.Int.fsti').read_text()
new=(w/'head/FStar.Int.fsti').read_text()
def block(s):
    a=s.index('let div (#n:pos)')
    b=s.index('\nval div_underspec:',a)
    return s[a:b]
ob,nb=block(old),block(new)
def split(b):
    m=re.search(r'\n=\s*',b)
    if not m: raise SystemExit('cannot find body boundary')
    return b[:m.start()], b[m.start():]
op,obody=split(ob); np,nbody=split(nb)
blocks={'C00':op+obody,'C10':op+nbody,'C01':np+obody,'C11':np+nbody}
for k,v in blocks.items():
    d=w/k; d.mkdir(); (d/'FStar.Int.fsti').write_text(new.replace(nb,v,1)); (w/f'{k}.div.txt').write_text(v)
(w/'old.div.txt').write_text(ob); (w/'new.div.txt').write_text(nb)
PY
cp "$WORK"/*.div.txt "$OUT/"

printf 'case,exit_code,verified_marker,error_marker\n' > "$OUT/results.csv"
verify_one(){
  name=$1; file=$2; inc=$3
  set +e
  "$FSTAR" --include "$inc" "$file" > "$OUT/$name.log" 2>&1
  ec=$?
  set -e
  v=$(grep -c 'Verified module' "$OUT/$name.log" || true)
  e=$(grep -ciE 'error|failed' "$OUT/$name.log" || true)
  printf '%s,%s,%s,%s\n' "$name" "$ec" "$v" "$e" >> "$OUT/results.csv"
}
# Exact historical source endpoints use their corresponding source tree as include context.
verify_one H0 "$WORK/parent/FStar.Int.fsti" "$WORK/up/ulib"
verify_one H1 "$WORK/head/FStar.Int.fsti" "$WORK/up/ulib"
# Fixed-head counterfactuals vary only the div definition block.
for c in C00 C10 C01 C11; do verify_one "$c" "$WORK/$c/FStar.Int.fsti" "$WORK/up/ulib"; done

cat > "$OUT/MANIFEST.md" <<EOF
# r18 F* blind-selected H055 replay
Upstream: FStarLang/FStar
Parent: $PARENT
Head: $HEAD
Blind label X5 was frozen before unblinding.
Verifier: historical-adjacent official F* release $RELEASE.
H0/H1 are exact historical FStar.Int.fsti bytes. C00/C10/C01/C11 use the head file context and replace only the `let div` definition block. C00 old contract+old body; C10 old contract+new body; C01 new contract+old body; C11 new contract+new body. The hybrids are causal counterfactuals, not historical commits.
EOF
sha256sum "$OUT"/* > "$OUT/SHA256SUMS.txt" || true
cat "$OUT/results.csv"
