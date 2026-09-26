#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out
rm -rf "$OUT" && mkdir -p "$OUT"
cat > "$OUT/C00.dfy" <<'EOF'
method UpdateElements(a: array<int>)
  requires a.Length >=9
  modifies a
  ensures old(a[4]) +3 == a[4]
  ensures a[8] == old(a[8])
  ensures a[7]==516
{
  a[4], a[8] := a[4] + 3, a[8] + 1;
  a[7], a[8] := 516, a[8] - 1;
}
EOF
cat > "$OUT/C10.dfy" <<'EOF'
method UpdateElements(a: array<int>)
  requires a.Length >=9
  modifies a
  ensures old(a[4]) +3 == a[4]
  ensures a[8] == old(a[8])
  ensures a[7]==516
{
  a[4] := a[4] + 3;
  a[7] := 516;
}
EOF
cat > "$OUT/C01.dfy" <<'EOF'
method UpdateElements(a: array<int>)
  requires a.Length >= 8
  modifies a
  ensures old(a[4]) +3 == a[4]
  ensures a[7]==516
  ensures forall i::0 <= i<a.Length ==> i != 7 && i != 4 ==> a[i] == old(a[i])
{
  a[4], a[8] := a[4] + 3, a[8] + 1;
  a[7], a[8] := 516, a[8] - 1;
}
EOF
cat > "$OUT/C11.dfy" <<'EOF'
method UpdateElements(a: array<int>)
  requires a.Length >= 8
  modifies a
  ensures old(a[4]) +3 == a[4]
  ensures a[7]==516
  ensures forall i::0 <= i<a.Length ==> i != 7 && i != 4 ==> a[i] == old(a[i])
{
  a[4] := a[4] + 3;
  a[7] := 516;
}
EOF
printf 'case,exit_code,verified,errors\n' > "$OUT/results.csv"
for c in C00 C10 C01 C11; do
  set +e
  dafny verify --verification-time-limit 30 "$OUT/$c.dfy" > "$OUT/$c.log" 2>&1
  ec=$?
  set -e
  summary=$(grep -E 'Dafny program verifier finished' "$OUT/$c.log" | tail -1 || true)
  verified=$(printf '%s' "$summary" | sed -nE 's/.*finished with ([0-9]+) verified.*/\1/p')
  errors=$(printf '%s' "$summary" | sed -nE 's/.*verified, ([0-9]+) error.*/\1/p')
  printf '%s,%s,%s,%s\n' "$c" "$ec" "${verified:-NA}" "${errors:-NA}" >> "$OUT/results.csv"
done
cat > "$OUT/MANIFEST.md" <<'EOF'
# r06 blind-selected technical replay
Source: ChuyueSun/Clover commit 85a1d0a92ab104b27f4c293cc972cd0b26da9595, parent 8544307b338b14d0fdfb17baf4ec35fccc0b44aa.
File: dataset/Dafny/textbook_algo/update_array/update_array_strong.dfy.
Selection: S04 was labeled X5 while repository/commit/title were blinded and the label was frozen before unblinding. This case is a CloverBench dataset example, not production evidence; replay is mandatory under the frozen protocol and is interpreted only as a technical code-contract coupling stress test.
C00 exact old function; C11 exact new function; C10 old contract/new body; C01 new contract/old body.
Verifier: Dafny 4.11.0 from the generic public worker environment.
EOF
sha256sum "$OUT"/*.dfy "$OUT"/*.log "$OUT/results.csv "$OUT/MANIFEST.md" > "$OUT/SHA256SUMS.txt"
cat "$OUT/results.csv"
