#!/usr/bin/env bash
# S1 (online, foreground) then S2 (offline 5 min -> reconnect -> flush) on Nox.
# Times are logged to qa/out/scenario_log.txt; verdicts go to qa/out/EVIDENCE.md.
A="/d/Program Files/Nox/bin/nox_adb.exe"; S=127.0.0.1:62001
ROOT=/d/Fleet_managment_project; L=$ROOT/qa/out/scenario_log.txt
PY="$ROOT/fleet-management/venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8
TRIP=$("$PY" -c "import json;print(json.load(open('$ROOT/qa/out/fixtures.json'))['orgs']['acme-bus-co']['nox_trip']['id'])")
buf() { timeout 20 "$A" -s $S shell "sqlite3 /data/data/com.fleet.fleet_driver_app/databases/ping_buffer.db 'select count(*) from pings;'" | tr -d '\r'; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
wait_until() { while [ "$(date -u +%s)" -lt "$(date -u -d "$1" +%s)" ]; do sleep 5; done; }

S1_FROM=2026-11-22T16:34:30Z
wait_until 2026-11-22T16:40:30Z
S1_TO=$(now); echo "S1_end $S1_TO buffer=$(buf)" >> $L
timeout 20 "$A" -s $S shell "svc wifi disable; svc data disable"
S2_OFF=$(now); echo "S2_offline $S2_OFF" >> $L
sleep 150; echo "S2_mid $(now) buffer=$(buf)" >> $L
sleep 150
timeout 20 "$A" -s $S shell "svc wifi enable; svc data enable"
S2_ON=$(now); echo "S2_online $S2_ON buffer=$(buf)" >> $L
for i in $(seq 1 18); do sleep 5; b=$(buf); [ "$b" = "0" ] && break; done
echo "S2_drained $(now) buffer=$(buf)" >> $L
sleep 30
cd $ROOT
"$PY" qa/verify_trip.py $TRIP $S1_FROM $S1_TO "S1 online foreground"
"$PY" qa/verify_trip.py $TRIP $S2_OFF $S2_ON "S2 offline window (buffered, flushed after reconnect)"
"$PY" qa/verify_trip.py $TRIP $S2_ON $(now) "S2 after reconnect"
echo "S1S2 done $(now)" >> $L
