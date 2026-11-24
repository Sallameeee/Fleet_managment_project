#!/usr/bin/env bash
# BUFFER CAP test on Nox (qa_ driver, qa_ org, live backend). The app keeps at
# most 5000 offline fixes (PingBuffer.cap) and trims the OLDEST rows (lowest id)
# whenever it inserts one more. This script:
#   1. cuts the network on an active trip and lets the app buffer real fixes
#      for WAIT_S seconds (the start of the "outage");
#   2. injects N synthetic fixes (tagged speed=12.34, heading=359.9, same spot)
#      straight into ping_buffer.db, so the queue holds > 5000;
#   3. waits for the app's next real fix (that insert trims to the cap) and
#      records exactly which rows were dropped;
#   4. restores the network WITHOUT opening the app, waits for the drain, and
#      checks what the server and the History API show for the missing part.
# Usage: TRIP=<id> N=5000 WAIT_S=150 bash qa/nox_bufcap.sh
A="/d/Program Files/Nox/bin/nox_adb.exe"; S=127.0.0.1:62001
OUTD=/d/Fleet_managment_project/qa/out; L=$OUTD/bufcap_log.txt
PY="/d/Fleet_managment_project/fleet-management/venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8
cd /d/Fleet_managment_project
N=${N:-5000}; WAIT_S=${WAIT_S:-150}
DB=/data/data/com.fleet.fleet_driver_app/databases/ping_buffer.db
sh_() { timeout 60 "$A" -s $S shell "$1" | tr -d '\r'; }
q() { sh_ "sqlite3 $DB \"$1\""; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

echo "BUFCAP trip=$TRIP n=$N wait_s=$WAIT_S start $(now)" > $L
echo "before: buf=$(q 'select count(*) from pings') db_bytes=$(sh_ "stat -c %s $DB")" >> $L
sh_ "svc wifi disable; svc data disable"; OFF=$(now); echo "OFF $OFF" >> $L
sleep "$WAIT_S"
# The real offline fixes so far: the start of the outage.
q "select id||'|'||recorded_at from pings where trip_id='$TRIP' order by id" > $OUTD/bufcap_real_before.txt
echo "real offline fixes buffered: $(wc -l < $OUTD/bufcap_real_before.txt) (first $(head -1 $OUTD/bufcap_real_before.txt), last $(tail -1 $OUTD/bufcap_real_before.txt))" >> $L
LAST=$(q "select lat||' '||lng from pings where trip_id='$TRIP' order by id desc limit 1")
LAT=${LAST% *}; LNG=${LAST#* }
# N synthetic fixes, 20 ms apart, starting now (all still <= now+10 min when flushed).
START=$(date -u +%Y-%m-%dT%H:%M:%S)
cat > $OUTD/bufcap_inject.sql <<SQL
BEGIN;
WITH RECURSIVE k(n) AS (SELECT 0 UNION ALL SELECT n+1 FROM k WHERE n < $N - 1)
INSERT INTO pings(trip_id, recorded_at, lat, lng, speed, heading, battery, net_state)
SELECT '$TRIP', strftime('%Y-%m-%dT%H:%M:%f', '$START', '+' || (n * 0.02) || ' seconds') || 'Z', $LAT, $LNG, 12.34, 359.9, 80, 'offline' FROM k;
COMMIT;
SQL
"$A" -s $S push $OUTD/bufcap_inject.sql /data/local/tmp/bufcap_inject.sql > /dev/null
sh_ "sqlite3 $DB < /data/local/tmp/bufcap_inject.sql"
echo "INJECTED $(now): total=$(q 'select count(*) from pings') injected=$(q "select count(*) from pings where speed=12.34 and heading=359.9") db_bytes=$(sh_ "stat -c %s $DB")" >> $L
# Next real fix -> enqueue -> trim to the cap.
for i in $(seq 1 24); do
  sleep 5
  c=$(q 'select count(*) from pings')
  if [ "$c" -le 5000 ]; then break; fi
done
q "select id||'|'||recorded_at from pings where trip_id='$TRIP' and not (speed=12.34 and heading=359.9) order by id" > $OUTD/bufcap_real_after.txt
echo "TRIMMED $(now): total=$(q 'select count(*) from pings') injected_left=$(q "select count(*) from pings where speed=12.34 and heading=359.9") real_left=$(wc -l < $OUTD/bufcap_real_after.txt) min_id=$(q 'select min(id) from pings') db_bytes=$(sh_ "stat -c %s $DB")" >> $L
sleep 20
sh_ "svc wifi enable; svc data enable"; ON=$(now); T_ON=$(date +%s); echo "ON $ON (app NOT opened)" >> $L
for i in $(seq 1 240); do
  sleep 5
  b=$(q 'select count(*) from pings')
  if [ "$b" -le 1 ]; then break; fi
  if [ $(( i % 12 )) = 0 ]; then echo "DRAINING $(now) buf=$b" >> $L; fi
done
echo "DRAINED $(now) after $(( $(date +%s) - T_ON ))s buf=$(q 'select count(*) from pings')" >> $L
sleep 30
"$PY" qa/bufcap_verify.py "$TRIP" "$OFF" "$ON" >> $L 2>&1
echo "BUFCAP done $(now)" >> $L
