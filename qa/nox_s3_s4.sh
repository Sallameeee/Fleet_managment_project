#!/usr/bin/env bash
# S3 (swiped away, online) -> S3b (swiped + offline -> reconnect) ->
# S4 (screen locked 10 min, Doze forced half-way) -> S4b (locked + offline -> reconnect).
A="/d/Program Files/Nox/bin/nox_adb.exe"; S=127.0.0.1:62001
L=/d/Fleet_managment_project/qa/out/scenario_log.txt
PY="/d/Fleet_managment_project/fleet-management/venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8
cd /d/Fleet_managment_project
TRIP=$("$PY" -c "import json;print(json.load(open('D:/Fleet_managment_project/qa/out/fixtures.json'))['orgs']['acme-bus-co']['nox_trip']['id'])")
echo "trip=$TRIP" >> $L
sh_() { timeout 25 "$A" -s $S shell "$1" | tr -d '\r'; }
buf() { sh_ "sqlite3 /data/data/com.fleet.fleet_driver_app/databases/ping_buffer.db 'select count(*) from pings;'"; }
mem() { sh_ "dumpsys meminfo com.fleet.fleet_driver_app | grep TOTAL | head -1" | awk '{print $2}'; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
v() { "$PY" qa/verify_trip.py "$TRIP" "$1" "$2" "$3" > /dev/null; }

S3_FROM=2026-11-22T16:54:00Z
sleep 180; S3_TO=$(now); echo "S3_end $S3_TO mem_kb=$(mem) svc=$(sh_ 'dumpsys activity services com.fleet.fleet_driver_app | grep -c isForeground=true')" >> $L
v $S3_FROM $S3_TO "S3 swiped away from recents (online)"
sh_ "svc wifi disable; svc data disable"; S3B_OFF=$(now); echo "S3b_offline $S3B_OFF" >> $L
sleep 120; echo "S3b_mid $(now) buffer=$(buf)" >> $L
sh_ "svc wifi enable; svc data enable"; S3B_ON=$(now); echo "S3b_online $S3B_ON" >> $L
sleep 45; v $S3B_OFF $S3B_ON "S3b swiped + offline window"; v $S3B_ON $(now) "S3b after reconnect (no reopen)"

sh_ "input keyevent 26"; S4_FROM=$(now); echo "S4_locked $S4_FROM mem_kb=$(mem)" >> $L
sleep 300
sh_ "dumpsys battery unplug; dumpsys deviceidle force-idle"; echo "S4_doze $(now) state=$(sh_ 'dumpsys deviceidle get deep')" >> $L
sleep 300; S4_TO=$(now); echo "S4_end $S4_TO doze=$(sh_ 'dumpsys deviceidle get deep') mem_kb=$(mem)" >> $L
v $S4_FROM $S4_TO "S4 screen locked 10 min (Doze forced for last 5 min)"
sh_ "svc wifi disable; svc data disable"; S4B_OFF=$(now); echo "S4b_offline $S4B_OFF" >> $L
sleep 120; echo "S4b_mid $(now) buffer=$(buf)" >> $L
sh_ "svc wifi enable; svc data enable"; S4B_ON=$(now); echo "S4b_online $S4B_ON" >> $L
sleep 45; v $S4B_OFF $S4B_ON "S4b locked + offline window"; v $S4B_ON $(now) "S4b after reconnect (still locked)"
sh_ "dumpsys deviceidle unforce; dumpsys battery reset"; echo "S3S4 done $(now) mem_kb=$(mem)" >> $L
