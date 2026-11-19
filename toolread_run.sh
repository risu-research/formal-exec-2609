#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -lt 1 ]; then echo 'Usage: run.sh {dev|holdout} [output_dir]' >&2; exit 2; fi
PART="$1"
if [ "$PART" != dev ] && [ "$PART" != holdout ]; then echo "Invalid partition: $PART" >&2; exit 2; fi
if [ "$#" -ge 2 ]; then OUT="$2"; else OUT="$ROOT/_runs/$PART"; fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
PORT="$(printenv REVOCATION_SOURCE_PORT || echo 8778)"
cd "$OUT"
python3 "$ROOT/toolread_runner.py" --partition plan
python3 "$ROOT/toolread_source_service.py" --db "$OUT/evidence.sqlite" --port "$PORT" > service.log 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT
READY=0
for i in $(seq 1 50); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/health', timeout=2).close()" >/dev/null 2>&1; then READY=1; break; fi
  sleep 1
done
test "$READY" = 1
python3 "$ROOT/toolread_runner.py" --partition "$PART" --endpoint "http://127.0.0.1:$PORT" | tee runner.log
kill "$PID"
wait "$PID" || true
trap - EXIT
python3 - <<'PY'
import sqlite3
with sqlite3.connect('evidence.sqlite') as c:
    status=c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    assert status[0]==0, status
    mode=c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
    assert mode.lower()=='delete',mode
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    print('SQL_COMMITTED',c.execute('SELECT count(*) FROM effects').fetchone()[0])
PY
python3 "$ROOT/toolread_auditor.py" --db evidence.sqlite \
  --oracle "tool_read_$PART""_oracle.json" --acks "tool_read_$PART""_acks.json" \
  --out "tool_read_$PART""_audit.json" | tee audit.log
sha256sum evidence.sqlite "tool_read_$PART""_audit.json" > SHA256SUMS.txt
echo "COMPLETE: $OUT"
