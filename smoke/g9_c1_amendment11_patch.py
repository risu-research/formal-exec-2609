#!/usr/bin/env python3
"""Derive G9 C1 V10 from frozen V9: create two aliases using original container owner."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys

V9_BLOB = 'dd6e733119b4a00df9ccfaa5e99d18c0dd27b50a'
OLD = '''ln -s "$(basename "$VFILE")" "$OLD_VFILE"
ln -s "$(basename "$SRAM")" "$OLD_SRAM"
'''
NEW = '''# The historical FIRRTL generator used the pinned container owner for $GEN.
# Reuse that same pinned image/owner to create ONLY the two original-byte aliases.
docker run --rm -v "$ROCKET":/work -w /work "$JAVA8_IMAGE" bash -lc '
  set -euo pipefail
  GEN_IN=/work/vsim/generated-src
  test ! -e "$GEN_IN/rocketchip.DefaultConfigWithRVFIMonitors.v" && test ! -L "$GEN_IN/rocketchip.DefaultConfigWithRVFIMonitors.v"
  test ! -e "$GEN_IN/rocketchip.DefaultConfigWithRVFIMonitors.behav_srams.v" && test ! -L "$GEN_IN/rocketchip.DefaultConfigWithRVFIMonitors.behav_srams.v"
  ln -s freechips.rocketchip.system.DefaultConfigWithRVFIMonitors.v "$GEN_IN/rocketchip.DefaultConfigWithRVFIMonitors.v"
  ln -s freechips.rocketchip.system.DefaultConfigWithRVFIMonitors.behav_srams.v "$GEN_IN/rocketchip.DefaultConfigWithRVFIMonitors.behav_srams.v"
'
'''

def blob(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit('usage: g9_c1_amendment11_patch.py V9_RUNNER V10_RUNNER DIFF')
    src, dst, diff = map(Path, sys.argv[1:])
    if len({src.resolve(), dst.resolve(), diff.resolve()}) != 3 or dst.exists() or diff.exists():
        raise SystemExit('DISTINCT_NONEXISTING_DESTINATIONS_REQUIRED')
    original = src.read_bytes()
    if blob(original) != V9_BLOB:
        raise SystemExit('FROZEN_V9_BLOB_MISMATCH')
    old = original.decode('utf-8')
    if old.count(OLD) != 1:
        raise SystemExit(f'ALIAS_PAIR_ANCHOR_COUNT_{old.count(OLD)}')
    new = old.replace(OLD, NEW, 1)
    if not new.endswith('\n'):
        raise SystemExit('MISSING_TERMINAL_NEWLINE')
    delta = ''.join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile='frozen-V9-runner', tofile='derived-V10-runner'))
    dst.write_bytes(new.encode('utf-8'))
    shutil.copymode(src, dst)
    diff.write_text(delta, encoding='utf-8')
    print(json.dumps({'v9_blob': V9_BLOB, 'v10_blob': blob(new.encode('utf-8')), 'replacements': 1,
                      'diff_sha256': hashlib.sha256(delta.encode('utf-8')).hexdigest()}, sort_keys=True))

if __name__ == '__main__':
    main()
