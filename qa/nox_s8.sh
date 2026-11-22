#!/usr/bin/env bash
# S8: a ~1 h trip with the network OFF the whole time on Nox (fixed APK).
# Samples the app buffer / memory / server state every 5 min, snapshots every
# buffered fix + heartbeat BEFORE reconnecting, then measures the drain and
# compares the server rows with that snapshot one by one (qa/s8_verify.py).
A="/d/Program Files/Nox/bin/nox_adb.exe"; S=127.0.0.1:62001
OUTD=/d/Fleet_managment_project/qa/out; L=$OUTD/s8_log.txt
PY="/d/Fleet_managment_project/fleet-management/venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8
cd /d/Fleet_managment_project
OFF_MIN=${OFF_MIN:-60}
DB=/data/data/com.fleet.fleet_driver_app/databases/ping_buffer.db
sh_() { timeout 30 "$A" -s $S shell "$1" | tr -d '\r'; }
q() { sh_ "sqlite3 $DB \"$1\""; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
TRIP=$("$PY" -c "import json;print(json.load(open('D:/Fleet_managment_project/qa/out/fixtures.json'))['orgs']['acme-bus-co']['s8_trip']['id'])")
echo "S8 trip=$TRIP off_min=$OFF_MIN" > $L
srv() { "$PY" -c "
import sys; sys.path.insert(0,'qa')
from qa_lib import sb, sb_count
t=sb('GET','trips?select=status,end_reason&id=eq.$TRIP')[0]
n=sb_count('location_pings?select=id&trip_id=eq.$TRIP')
al=[a['type'] for a in sb('GET','alerts?select=type&trip_id=eq.$TRIP&type=in.(connection_lost,connection_restored,trip_ended,offline)&order=occurred_at')]
print(f\"server: status={t['status']} end_reason={t['end_reason']} pings={n} lifecycle={al}\")"; }

sleep 60
echo "ONLINE_BASELINE $(now) $(srv)" >> $L
sh_ "svc wifi disable; svc data disable"; OFF=$(now); echo "OFF $OFF" >> $L
END=$(( $(date +%s) + OFF_MIN*60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  sleep 300
  echo "T+$(( ( $(date +%s) - $(date -d $OFF +%s) )/60 ))min $(now) buf_pings=$(q 'select count(*) from pings') buf_hb=$(q 'select count(*) from heartbeats') pid=$(sh_ 'pidof com.fleet.fleet_driver_app') fg=$(sh_ 'dumpsys activity services com.fleet.fleet_driver_app | grep -c isForeground=true') pss_kb=$(sh_ 'dumpsys meminfo com.fleet.fleet_driver_app | grep TOTAL | head -1' | awk '{print $2}') | $(srv)" >> $L
done
# snapshot EVERY buffered row before reconnecting (ground truth for the comparison)
q "select recorded_at from pings where trip_id='$TRIP' order by recorded_at" > $OUTD/s8_buffer_pings.txt
q "select sent_at from heartbeats where trip_id='$TRIP' order by sent_at" > $OUTD/s8_buffer_heartbeats.txt
echo "SNAPSHOT $(now) pings=$(wc -l < $OUTD/s8_buffer_pings.txt) heartbeats=$(wc -l < $OUTD/s8_buffer_heartbeats.txt) other_trip_rows=$(q "select count(*) from pings where trip_id<>'$TRIP'")" >> $L
sh_ "svc wifi enable; svc data enable"; ON=$(now); echo "ON $ON" >> $L
T_ON=$(date +%s)
for i in $(seq 1 120); do sleep 2; b=$(q 'select count(*) from pings'); h=$(q 'select count(*) from heartbeats'); if [ "$b" -le 1 ] && [ "$h" = "0" ]; then break; fi; done
echo "DRAINED $(now) after $(( $(date +%s) - T_ON ))s buf_pings=$(q 'select count(*) from pings') buf_hb=$(q 'select count(*) from heartbeats')" >> $L
sleep 70
echo "AFTER $(now) $(srv)" >> $L
"$PY" qa/s8_verify.py "$TRIP" "$OFF" "$ON" >> $L 2>&1
echo "S8 done $(now)" >> $L
