#!/usr/bin/env bash
# Re-test with the fixed APK: S1b online 2 min -> S2 offline 3 min -> reconnect/flush
# -> S5 force-stop 2 min -> reopen -> recovery. Verdicts -> qa/out/EVIDENCE.md.
A="/d/Program Files/Nox/bin/nox_adb.exe"; S=127.0.0.1:62001
L=/d/Fleet_managment_project/qa/out/scenario_log.txt
PY="/d/Fleet_managment_project/fleet-management/venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8
cd /d/Fleet_managment_project
TRIP=$("$PY" -c "import json;print(json.load(open('D:/Fleet_managment_project/qa/out/fixtures.json'))['orgs']['acme-bus-co']['nox_trip2']['id'])")
echo "retest trip=$TRIP" >> $L
sh_() { timeout 25 "$A" -s $S shell "$1" | tr -d '\r'; }
buf() { sh_ "sqlite3 /data/data/com.fleet.fleet_driver_app/databases/ping_buffer.db 'select count(*) from pings;'"; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
v() { "$PY" qa/verify_trip.py "$TRIP" "$1" "$2" "$3" > /dev/null; }

F1=$(now); sleep 120; T1=$(now); v $F1 $T1 "RETEST S1b online (fixed APK)"
sh_ "svc wifi disable; svc data disable"; OFF=$(now); echo "RETEST_offline $OFF buffer=$(buf)" >> $L
for i in 1 2 3 4 5 6; do sleep 30; echo "RETEST_offline +$((i*30))s buffer=$(buf)" >> $L; done
sh_ "svc wifi enable; svc data enable"; ON=$(now); echo "RETEST_online $ON buffer=$(buf)" >> $L
for i in $(seq 1 24); do sleep 5; b=$(buf); [ "$b" = "0" ] && break; done
echo "RETEST_drained $(now) buffer=$(buf)" >> $L
sleep 30
v $OFF $ON "RETEST S2 offline window (fixed APK)"
v $ON $(now) "RETEST S2 after reconnect (fixed APK)"
# S5: force stop -> sampling stops by design -> reopen -> trip restores
sh_ "am force-stop com.fleet.fleet_driver_app"; FS=$(now); echo "S5_force_stop $FS" >> $L
sleep 150
sh_ "monkey -p com.fleet.fleet_driver_app -c android.intent.category.LAUNCHER 1" > /dev/null; RO=$(now); echo "S5_reopened $RO" >> $L
sleep 60
v $FS $(now) "S5 force-stop 150 s then reopen"
"$PY" - <<PYEOF >> $L
import sys; sys.path.insert(0, "qa")
from qa_lib import sb
t = sb("GET", "trips?select=status&id=eq.$TRIP")[0]
lost = sb("GET", "alerts?select=type,occurred_at&trip_id=eq.$TRIP&type=in.(connection_lost,connection_restored)&occurred_at=gte.$FS&order=occurred_at")
print("S5_result trip_status=%s lifecycle=%s" % (t["status"], [(a["type"], a["occurred_at"][11:19]) for a in lost]))
PYEOF
echo "RETEST done $(now)" >> $L
