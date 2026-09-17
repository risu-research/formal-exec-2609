#!/usr/bin/env python3
from __future__ import annotations
import difflib, hashlib, json, sys
from pathlib import Path

if len(sys.argv) != 4:
    raise SystemExit(f"usage: {sys.argv[0]} INPUT_V10 OUTPUT_V11 PATCH_OUT")

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
patch_path = Path(sys.argv[3])
text = src.read_text()

needle = """# Reproduce the successor riscv-formal Rocket->RVFI IL transformation.\n(\n  cd /tmp/g9-c1-riscv/cores/rocket\n  yosys -v2 -l rocket-chip.yslog rocket-chip.ys\n)\n"""
insert = r'''# Amendment12: exact generated-module-name compatibility only.
# Generated Rocket/SRAM bytes are sealed before this transform and are never edited.
YS=/tmp/g9-c1-riscv/cores/rocket/rocket-chip.ys
ORIG_YS="$OUT/historical-rocket-chip.ys"
PATCHED_YS="$OUT/compat-rocket-chip.ys"
cp "$YS" "$ORIG_YS"
python3 - "$VFILE" <<'PY'
import re,sys
p=sys.argv[1]
s=open(p).read()
mods=re.findall(r'(?m)^module\s+([A-Za-z0-9_$]+)\s*\(',s)
assert mods.count('RocketTile') == 1, ('RocketTile declarations',mods.count('RocketTile'))
start=s.index('module RocketTile(')
end=s.index('\nendmodule',start)
block=s[start:end]
assert block.count('RocketWithRVFI core (') == 1, 'expected exact RocketWithRVFI core instance in RocketTile'
assert 'module RocketTile_rocket(' not in s, 'unexpected historical module already present'
print('G9_C1_V11_GENERATED_STRUCTURE_PASS')
PY
test "$(grep -c '^hierarchy -top RocketTile_rocket$' "$YS")" = 1
test "$(grep -c '^cd RocketTile_rocket$' "$YS")" = 1
sed -i \
  -e 's/^hierarchy -top RocketTile_rocket$/hierarchy -top RocketTile/' \
  -e 's/^cd RocketTile_rocket$/cd RocketTile/' \
  "$YS"
test "$(grep -c '^hierarchy -top RocketTile$' "$YS")" = 1
test "$(grep -c '^cd RocketTile$' "$YS")" = 1
test "$(grep -c 'RocketTile_rocket' "$YS")" = 0
cp "$YS" "$PATCHED_YS"
set +e
diff -u "$ORIG_YS" "$PATCHED_YS" > "$OUT/rocket-chip-compat.diff"
diff_rc=$?
set -e
test "$diff_rc" = 1
python3 - "$OUT/rocket-chip-compat.diff" <<'PY'
import sys
s=open(sys.argv[1]).read()
removed=[x for x in s.splitlines() if x.startswith('-') and not x.startswith('---')]
added=[x for x in s.splitlines() if x.startswith('+') and not x.startswith('+++')]
assert removed == ['-hierarchy -top RocketTile_rocket','-cd RocketTile_rocket'], removed
assert added == ['+hierarchy -top RocketTile','+cd RocketTile'], added
print('G9_C1_V11_COMPAT_DIFF_PASS')
PY
sha256sum "$ORIG_YS" "$PATCHED_YS" "$OUT/rocket-chip-compat.diff" > "$OUT/rocket-chip-compat-sha256.txt"

# Reproduce the successor riscv-formal Rocket->RVFI IL transformation.
(
  cd /tmp/g9-c1-riscv/cores/rocket
  yosys -v2 -l rocket-chip.yslog rocket-chip.ys
)
'''

if text.count(needle) != 1:
    raise SystemExit(f"expected exactly one Yosys execution block, found {text.count(needle)}")
out = text.replace(needle, insert, 1)
dst.write_text(out)

patch = ''.join(difflib.unified_diff(
    text.splitlines(True), out.splitlines(True),
    fromfile='frozen-V10-runner', tofile='derived-V11-runner'))
patch_path.write_text(patch)

blob = hashlib.sha1((f"blob {len(out.encode())}\0".encode()) + out.encode()).hexdigest()
print(json.dumps({
    'schema':'g9-c1-amendment12-derivation-v1',
    'replacements':1,
    'input_sha256':hashlib.sha256(text.encode()).hexdigest(),
    'output_sha256':hashlib.sha256(out.encode()).hexdigest(),
    'v11_blob':blob,
    'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),
},sort_keys=True))
