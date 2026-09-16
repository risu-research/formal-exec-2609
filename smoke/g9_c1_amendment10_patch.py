#!/usr/bin/env python3
"""Amendment10: derive V9 from frozen V8 with two exact-byte Yosys file aliases only."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys

V8_BLOB = '4ccae1f7b2b994b2b9e054575ea0629608176954'


def blob_hash(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


ANCHOR = '# Reproduce the successor riscv-formal Rocket->RVFI IL transformation.\n(\n'
REPLACEMENT = '''# Restore only the two historical read_verilog basenames as exact-byte aliases.
OLD_VFILE="$GEN/rocketchip.DefaultConfigWithRVFIMonitors.v"
OLD_SRAM="$GEN/rocketchip.DefaultConfigWithRVFIMonitors.behav_srams.v"
test ! -e "$OLD_VFILE" && test ! -L "$OLD_VFILE"
test ! -e "$OLD_SRAM" && test ! -L "$OLD_SRAM"
ln -s "$(basename "$VFILE")" "$OLD_VFILE"
ln -s "$(basename "$SRAM")" "$OLD_SRAM"
test -L "$OLD_VFILE" && test -L "$OLD_SRAM"
test "$(realpath "$OLD_VFILE")" = "$(realpath "$VFILE")"
test "$(realpath "$OLD_SRAM")" = "$(realpath "$SRAM")"
cmp -s "$OLD_VFILE" "$VFILE"
cmp -s "$OLD_SRAM" "$SRAM"
{
  printf 'verilog_alias=%s\\nverilog_target=%s\\n' "$OLD_VFILE" "$(readlink "$OLD_VFILE")"
  printf 'sram_alias=%s\\nsram_target=%s\\n' "$OLD_SRAM" "$(readlink "$OLD_SRAM")"
} > "$OUT/yosys-input-aliases.txt"
stat -c '%n %F %s %N' "$OLD_VFILE" "$OLD_SRAM" > "$OUT/yosys-input-alias-stat.txt"
sha256sum "$OLD_VFILE" "$VFILE" "$OLD_SRAM" "$SRAM" > "$OUT/yosys-input-alias-sha256.txt"
sha256sum /tmp/g9-c1-riscv/cores/rocket/rocket-chip.ys > "$OUT/historical-yosys-script-sha256.txt"

# Reproduce the successor riscv-formal Rocket->RVFI IL transformation.
(
'''


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit('usage: g9_c1_amendment10_patch.py V8_RUNNER V9_RUNNER DIFF_FILE')
    src, dst, diff = map(Path, sys.argv[1:])
    if len({src.resolve(), dst.resolve(), diff.resolve()}) != 3 or dst.exists() or diff.exists():
        raise SystemExit('DISTINCT_NONEXISTING_DESTINATIONS_REQUIRED')
    old_raw = src.read_bytes()
    if blob_hash(old_raw) != V8_BLOB:
        raise SystemExit('FROZEN_V8_BLOB_MISMATCH')
    old = old_raw.decode('utf-8')
    n = old.count(ANCHOR)
    if n != 1:
        raise SystemExit(f'HISTORICAL_YOSYS_ANCHOR_COUNT_{n}')
    new = old.replace(ANCHOR, REPLACEMENT, 1)
    if not new.endswith('\n') or new == old:
        raise SystemExit('V9_DERIVATION_INVARIANT_FAILED')
    patch = ''.join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                         fromfile='frozen-V8-runner', tofile='derived-V9-runner'))
    dst.write_bytes(new.encode('utf-8'))
    shutil.copymode(src, dst)
    diff.write_text(patch, encoding='utf-8')
    print(json.dumps({'v8_blob': V8_BLOB, 'v9_blob': blob_hash(new.encode('utf-8')),
                      'replacements': 1, 'diff_sha256': hashlib.sha256(patch.encode('utf-8')).hexdigest()},
                     sort_keys=True))


if __name__ == '__main__':
    main()
