#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out; rm -rf "$OUT"; mkdir -p "$OUT"
HOST_OUT="$(pwd)/$OUT"
IMAGE=mtzguido/pulse-cuda-devcontainer:latest

docker pull "$IMAGE" > "$OUT/docker_pull.log" 2>&1
docker image inspect "$IMAGE" > "$OUT/docker_image_inspect.json"

docker run --rm -v "$HOST_OUT:/hostout" "$IMAGE" bash -lc '
set -euo pipefail
WORK=/tmp/r19; rm -rf "$WORK"; mkdir -p "$WORK"
HEAD=f49f839b96222f4498087332837aa974b425e9cc
PARENT=94df77ed89f4dc8ff0153ab70247c9374a2a89f0
UP=https://github.com/FStarLang/kuiper.git
git clone -q "$UP" "$WORK/k"
cd "$WORK/k"
git checkout -q "$HEAD"
git submodule update --init --recursive --depth 1
printf "head,%s\nparent,%s\n" "$HEAD" "$PARENT" > /hostout/historical_refs.csv
git rev-parse HEAD:FStar > /hostout/fstar_submodule_head.txt
git rev-parse "$PARENT:FStar" > /hostout/fstar_submodule_parent.txt

eval $(opam env)
make -j2 -f verify.mk .fstar.touch > /hostout/fstar_build.log 2>&1
FSTAR=$PWD/inst/bin/fstar.exe
"$FSTAR" --version > /hostout/verifier.txt 2>&1 || true

FILE=src/lib/kuiper/Kuiper.Array.Core.fst
git show "$HEAD:$FILE" > "$WORK/head.fst"
git show "$PARENT:$FILE" > "$WORK/parent.fst"
sha256sum "$WORK/head.fst" "$WORK/parent.fst" > /hostout/source_sha256.txt
python3 - "$WORK" <<"PY"
from pathlib import Path
import sys,re
w=Path(sys.argv[1]); old=(w/"parent.fst").read_text(); new=(w/"head.fst").read_text()
def block(s):
 a=s.index("fn gpu_array_free_gen")
 b=s.index("\nfn gpu_array_free",a)
 return s[a:b]
ob,nb=block(old),block(new)
def split(b):
 # contract prefix ends immediately before function body opening brace on its own line
 m=re.search(r"\n\{\n",b)
 if not m: raise SystemExit("body boundary not found")
 return b[:m.start()], b[m.start():]
op,obody=split(ob); np,nbody=split(nb)
for k,v in {"C00":op+obody,"C10":op+nbody,"C01":np+obody,"C11":np+nbody}.items():
 (w/f"{k}.fst").write_text(new.replace(nb,v,1)); (w/f"{k}.block.txt").write_text(v)
(w/"old.block.txt").write_text(ob);(w/"new.block.txt").write_text(nb)
PY
cp "$WORK"/*.block.txt /hostout/

FLAGS=(--include "$PWD/src" --warn_error -291 --warn_error -249-321 --warn_error @242@250 --z3version 4.13.3 --ext kuiper --ext __unrefine --ext no_krml_private --warn_error -288 --ext context_pruning_no_ambients --ext freshen)
printf "case,exit_code,verified_marker,error_marker\n" > /hostout/results.csv
runone(){
  n=$1; src=$2
  rm -rf "$WORK/cache-$n"; mkdir -p "$WORK/cache-$n"
  set +e
  "$FSTAR" "${FLAGS[@]}" --cache_dir "$WORK/cache-$n" --odir "$WORK/cache-$n" "$src" > "/hostout/$n.log" 2>&1
  ec=$?
  set -e
  v=$(grep -c "Verified module" "/hostout/$n.log" || true)
  e=$(grep -ciE "error|failed" "/hostout/$n.log" || true)
  printf "%s,%s,%s,%s\n" "$n" "$ec" "$v" "$e" >> /hostout/results.csv
}
# Historical endpoint files; same pinned historical toolchain from head CI submodule.
runone H0 "$WORK/parent.fst"
runone H1 "$WORK/head.fst"
for c in C00 C10 C01 C11; do runone "$c" "$WORK/$c.fst"; done
cat > /hostout/MANIFEST.md <<EOF
# r19 Kuiper blind-selected H006 replay
Upstream: FStarLang/kuiper
Parent: $PARENT
Head: $HEAD
Blind X5 label was frozen before unblinding. Upstream historical CI uses the repository-pinned FStar submodule and builds `.fstar.touch`; this replay follows that path inside the same CI container image family (`mtzguido/pulse-cuda-devcontainer:latest`). H0/H1 are exact historical `Kuiper.Array.Core.fst` bytes. Fixed-head C00/C10/C01/C11 replace only the `gpu_array_free_gen` block: old/new contract prefix crossed with old/new body. Hybrids are causal counterfactuals, not historical commits. Post-unblind limitation: the old body contains an explicit `admit()`, so this case is retained as protocol-mandated technical evidence but must be flagged as proof-hole-confounded rather than promoted uncritically as strong production confirmation.
EOF
cat /hostout/results.csv
'
sha256sum "$OUT"/* > "$OUT/SHA256SUMS.txt" || true
cat "$OUT/results.csv" 2>/dev/null || true
