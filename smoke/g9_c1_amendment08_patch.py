#!/usr/bin/env python3
"""Derive only the frozen G9 C1 Amendment08 Python-launcher V7 build runner.

Exact V6 identity and unique anchors are mandatory. This does not touch
scientific source, candidates, queries, horizons, or any earlier runner.
"""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys

V6_BLOB = '5536b46074b07fd94c3f3392fea72f946cd97819'


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


REPLACEMENTS = [
    (
        "DTC_PACKAGE='device-tree-compiler'\n",
        "DTC_PACKAGE='device-tree-compiler'\nPYTHON_LAUNCHER_PACKAGE='python-is-python3'\n",
    ),
    (
        '  -e DTC_PACKAGE="$DTC_PACKAGE" \\\n',
        '  -e DTC_PACKAGE="$DTC_PACKAGE" \\\n  -e PYTHON_LAUNCHER_PACKAGE="$PYTHON_LAUNCHER_PACKAGE" \\\n',
    ),
    (
        '    apt-cache policy "$DTC_PACKAGE" > /evidence/dtc-apt-policy.txt\n',
        '    apt-cache policy "$DTC_PACKAGE" > /evidence/dtc-apt-policy.txt\n'
        '    apt-cache policy "$PYTHON_LAUNCHER_PACKAGE" > /evidence/python-launcher-apt-policy.txt\n',
    ),
    (
        '    apt-get --print-uris --yes install "$DTC_PACKAGE" > /evidence/dtc-install-uris.txt 2>&1 || true\n',
        '    apt-get --print-uris --yes install "$DTC_PACKAGE" > /evidence/dtc-install-uris.txt 2>&1 || true\n'
        '    apt-get --print-uris --yes install "$PYTHON_LAUNCHER_PACKAGE" > /evidence/python-launcher-install-uris.txt 2>&1 || true\n',
    ),
    (
        '    DEBIAN_FRONTEND=noninteractive apt-get install -y "$MAKE_PACKAGE" "$GIT_PACKAGE" "$DTC_PACKAGE" > /evidence/apt-install-log.txt 2>&1\n',
        '    DEBIAN_FRONTEND=noninteractive apt-get install -y "$MAKE_PACKAGE" "$GIT_PACKAGE" "$DTC_PACKAGE" "$PYTHON_LAUNCHER_PACKAGE" > /evidence/apt-install-log.txt 2>&1\n',
    ),
    (
        '    sha256sum "$(command -v dtc)" > /evidence/container-dtc-binary-sha256.txt\n',
        '    sha256sum "$(command -v dtc)" > /evidence/container-dtc-binary-sha256.txt\n'
        '    command -v python > /evidence/container-python-path.txt\n'
        '    command -v python3 > /evidence/container-python3-path.txt\n'
        '    python --version > /evidence/container-python-version.txt 2>&1\n'
        '    python3 --version > /evidence/container-python3-version.txt 2>&1\n'
        '    readlink -f "$(command -v python)" > /evidence/container-python-resolved-path.txt\n'
        '    readlink -f "$(command -v python3)" > /evidence/container-python3-resolved-path.txt\n'
        '    sha256sum "$(command -v python)" > /evidence/container-python-launcher-sha256.txt\n'
        '    sha256sum "$(command -v python3)" > /evidence/container-python3-launcher-sha256.txt\n'
        '    sha256sum "$(readlink -f "$(command -v python)")" > /evidence/container-python-target-sha256.txt\n'
        '    sha256sum "$(readlink -f "$(command -v python3)")" > /evidence/container-python3-target-sha256.txt\n'
        '    dpkg-query -W -f="\\${Package}=\\${Version}\\n" "$PYTHON_LAUNCHER_PACKAGE" python3 > /evidence/container-python-packages.txt\n'
        '    head -n 1 /work/scripts/vlsi_mem_gen > /evidence/historical-memory-generator-shebang.txt\n'
        '    test "$(head -n 1 /work/scripts/vlsi_mem_gen)" = "#! /usr/bin/env python"\n',
    ),
]


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit('usage: g9_c1_amendment08_patch.py V6_RUNNER V7_RUNNER DIFF_FILE')
    src, dst, diff = map(Path, sys.argv[1:])
    if len({src.resolve(), dst.resolve(), diff.resolve()}) != 3:
        raise SystemExit('source, output and diff must be distinct')
    old_bytes = src.read_bytes()
    if git_blob(old_bytes) != V6_BLOB:
        raise SystemExit('FROZEN_V6_BLOB_MISMATCH: refuse patch')
    old = old_bytes.decode('utf-8')
    new = old
    for i, (before, after) in enumerate(REPLACEMENTS, 1):
        count = new.count(before)
        if count != 1:
            raise SystemExit(f'ANCHOR_{i}_COUNT_{count}: refuse patch')
        new = new.replace(before, after, 1)
    if new == old or not new.endswith('\n') or dst.exists() or diff.exists():
        raise SystemExit('DERIVATION_OR_DESTINATION_INVARIANT_FAILED')
    new_bytes = new.encode('utf-8')
    patch = ''.join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                         fromfile='frozen-V6-runner', tofile='derived-V7-runner'))
    dst.write_bytes(new_bytes)
    shutil.copymode(src, dst)
    diff.write_text(patch, encoding='utf-8')
    print(json.dumps({'v6_blob': V6_BLOB, 'v7_blob': git_blob(new_bytes),
                      'replacements': len(REPLACEMENTS), 'diff_sha256': hashlib.sha256(patch.encode()).hexdigest()},
                     sort_keys=True))


if __name__ == '__main__':
    main()
