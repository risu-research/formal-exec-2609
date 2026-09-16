#!/usr/bin/env python3
"""Deterministically derive G9 C1 V6 build-only runner from frozen V5 bytes.

No candidate source, formal query, property, horizon or history is read/changed.
This patcher is intentionally not a generic text editor: every original anchor
must occur exactly once, and the starting runner's Git blob is exact.
"""

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

V5_BLOB = '0d0a91eb57739228c4a2141126c1487ae8d81bf6'


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


REPLACEMENTS = [
    (
        'GIT_PACKAGE=\'git\'\n',
        'GIT_PACKAGE=\'git\'\nDTC_PACKAGE=\'device-tree-compiler\'\n',
    ),
    (
        '  -e GIT_PACKAGE="$GIT_PACKAGE" \\\n',
        '  -e GIT_PACKAGE="$GIT_PACKAGE" \\\n  -e DTC_PACKAGE="$DTC_PACKAGE" \\\n',
    ),
    (
        '    apt-cache policy git > /evidence/git-apt-policy.txt\n',
        '    apt-cache policy git > /evidence/git-apt-policy.txt\n'
        '    apt-cache policy "$DTC_PACKAGE" > /evidence/dtc-apt-policy.txt\n',
    ),
    (
        '    apt-get --print-uris --yes install "$GIT_PACKAGE" > /evidence/git-install-uris.txt 2>&1 || true\n',
        '    apt-get --print-uris --yes install "$GIT_PACKAGE" > /evidence/git-install-uris.txt 2>&1 || true\n'
        '    apt-get --print-uris --yes install "$DTC_PACKAGE" > /evidence/dtc-install-uris.txt 2>&1 || true\n',
    ),
    (
        '    DEBIAN_FRONTEND=noninteractive apt-get install -y "$MAKE_PACKAGE" "$GIT_PACKAGE" >/dev/null\n',
        '    DEBIAN_FRONTEND=noninteractive apt-get install -y "$MAKE_PACKAGE" "$GIT_PACKAGE" "$DTC_PACKAGE" > /evidence/apt-install-log.txt 2>&1\n',
    ),
    (
        '    sha256sum "$(command -v git)" > /evidence/container-git-binary-sha256.txt\n',
        '    sha256sum "$(command -v git)" > /evidence/container-git-binary-sha256.txt\n'
        '    command -v dtc > /evidence/container-dtc-path.txt\n'
        '    dtc --version > /evidence/container-dtc-version.txt\n'
        '    dpkg-query -W -f="\\${Package}=\\${Version}\\n" "$DTC_PACKAGE" > /evidence/container-dtc-package.txt\n'
        '    sha256sum "$(command -v dtc)" > /evidence/container-dtc-binary-sha256.txt\n',
    ),
    (
        'docker run --rm \\\n  -v "$ROCKET":/work \\\n',
        'timeout --signal=TERM --kill-after=30s 30m docker run --rm \\\n  -v "$ROCKET":/work \\\n',
    ),
]


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit('usage: g9_c1_amendment07_patch.py V5_RUNNER V6_RUNNER DIFF_FILE')
    src, dst, diff = map(Path, sys.argv[1:])
    if src.resolve() == dst.resolve() or src.resolve() == diff.resolve() or dst.resolve() == diff.resolve():
        raise SystemExit('source, derived runner and diff must be distinct')
    old_bytes = src.read_bytes()
    if git_blob(old_bytes) != V5_BLOB:
        raise SystemExit('FROZEN_V5_BLOB_MISMATCH: refuse patch')
    old = old_bytes.decode('utf-8')
    new = old
    for i, (before, after) in enumerate(REPLACEMENTS, 1):
        if new.count(before) != 1:
            raise SystemExit(f'ANCHOR_{i}_COUNT_{new.count(before)}: refuse patch')
        new = new.replace(before, after, 1)
    if new == old or not new.endswith('\n'):
        raise SystemExit('DERIVATION_INVARIANT_FAILED')
    if dst.exists() or diff.exists():
        raise SystemExit('DESTINATION_ALREADY_EXISTS: refuse overwrite')
    dst.write_bytes(new.encode('utf-8'))
    shutil.copymode(src, dst)
    patch = ''.join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                         fromfile='frozen-V5-runner', tofile='derived-V6-runner'))
    diff.write_text(patch, encoding='utf-8')
    print(json.dumps({'v5_blob': V5_BLOB, 'v6_blob': git_blob(new.encode('utf-8')),
                      'replacements': len(REPLACEMENTS), 'diff_sha256': hashlib.sha256(patch.encode()).hexdigest()},
                     sort_keys=True))


if __name__ == '__main__':
    main()
