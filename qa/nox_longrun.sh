#!/usr/bin/env bash
# LONG-OFFLINE run on Nox against the live backend (qa_ org only):
#   online baseline -> network OFF for OFF_MIN minutes (the app is swiped away by
#   hand early on; the screen is switched off at +LOCK_AT min) -> network ON WITHOUT
#   opening the app -> drain -> strict verification (qa/s8_verify.py, tag "lr").
# Samples every SAMPLE_S s: app buffer, foreground service, memory, CPU ticks of
# the app process, battery, and the server's view of the trip.
# Usage: TRIP=<id> OFF_MIN=210 LOCK_AT=60 bash qa/nox_longrun.sh
A="/d/Program Files/Nox/bin/nox_adb.exe"; S=127.0.0.1:62001
OUTD=/d/Fleet_managment_project/qa/out; L=$OUTD/lr_log.txt
PY="/d/Fleet_managment_project/fleet-management/venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8
cd /d/Fleet_managment_project
OFF_MIN=${OFF_MIN:-210}; LOCK_AT=${LOCK_AT:-60}; SAMPLE_S=${SAMPLE_S:-600}
PKG=com.fleet.fleet_driver_app
DB=/data/data/$PKG/databases/ping_buffer.db
sh_() { timeout 30 "$A" -s $S shell "$1" | tr -d '\r'; }
q() { sh_ "sqlite3 $DB \"$1\""; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
srv() { "$PY" -c "
import sys; sys.path.insert(0,'qa')
from qa_lib import sb, sb_count
t=sb('GET','trips?select=status,end_reason,ended_at&id=eq.$TRIP')[0]
n=sb_count('location_pings?select=id&trip_id=eq.$TRIP')
al=[a['type'] for a in sb('GET','alerts?select=type&trip_id=eq.$TRIP&type=in.(connection_lost,connection_restored,trip_ended)&order=occurred_at')]
print(f\"server: status={t['status']} end_reason={t['end_reason']} ended_at={t['ended_at']} pings={n} lifecycle={al}\")" 2>&1 | tail -1; }
ticks() { local p; p=$(sh_ "pidof $PKG"); [ -n "$p" ] && sh_ "cat /proc/$p/stat" | awk '{print $14+$15}' || echo "na"; }
sample() {
  echo "$1 $(now) buf_pings=$(q 'select count(*) from pings') buf_hb=$(q 'select count(*) from heartbeats') pid=$(sh_ "pidof $PKG") fg=$(sh_ "dumpsys activity services $PKG | grep -c isForeground=true") pss_kb=$(sh_ "dumpsys meminfo $PKG | grep TOTAL | head -1" | awk '{print $2}') cpu_ticks=$(ticks) screen=$(sh_ "dumpsys power | grep -o 'mWakefulness=[A-Za-z]*'") battery=$(sh_ "dumpsys battery | grep -E '^ *level' | tr -d ' '") db_bytes=$(sh_ "stat -c %s $DB 2>/dev/null") | $(srv)" >> $L
}

echo "LONGRUN trip=$TRIP off_min=$OFF_MIN lock_at=$LOCK_AT start $(now)" > $L
sh_ "dumpsys batterystats --reset" > /dev/null
sample ONLINE_START
sleep 180
sample ONLINE_BASELINE
sh_ "svc wifi disable; svc data disable"; OFF=$(now); OFF_S=$(date +%s); echo "OFF $OFF" >> $L
END=$(( OFF_S + OFF_MIN*60 )); LOCKED=0
while [ "$(date +%s)" -lt "$END" ]; do
  sleep 60
  el=$(( ( $(date +%s) - OFF_S ) / 60 ))
  if [ "$LOCKED" = "0" ] && [ "$el" -ge "$LOCK_AT" ]; then
    sh_ "input keyevent 26"; LOCKED=1; echo "SCREEN_OFF $(now) (power key: screen off + keyguard)" >> $L
  fi
  if [ $(( ( $(date +%s) - OFF_S ) % SAMPLE_S )) -lt 60 ]; then sample "T+${el}min"; fi
done
# ground truth: every buffered row, right before reconnecting
q "select recorded_at from pings where trip_id='$TRIP' order by recorded_at" > $OUTD/lr_buffer_pings.txt
q "select sent_at from heartbeats where trip_id='$TRIP' order by sent_at" > $OUTD/lr_buffer_heartbeats.txt
echo "SNAPSHOT $(now) pings=$(wc -l < $OUTD/lr_buffer_pings.txt) heartbeats=$(wc -l < $OUTD/lr_buffer_heartbeats.txt) other_trip_rows=$(q "select count(*) from pings where trip_id<>'$TRIP'") db_bytes=$(sh_ "stat -c %s $DB")" >> $L
sample BEFORE_ON
sh_ "svc wifi enable; svc data enable"; ON=$(now); T_ON=$(date +%s); echo "ON $ON (app NOT opened)" >> $L
for i in $(seq 1 360); do
  sleep 5
  b=$(q 'select count(*) from pings'); h=$(q 'select count(*) from heartbeats')
  if [ "$b" -le 1 ] && [ "$h" -le 1 ]; then break; fi
  if [ $(( i % 12 )) = 0 ]; then echo "DRAINING $(now) buf_pings=$b buf_hb=$h" >> $L; fi
done
echo "DRAINED $(now) after $(( $(date +%s) - T_ON ))s buf_pings=$(q 'select count(*) from pings') buf_hb=$(q 'select count(*) from heartbeats')" >> $L
sleep 30
sample AFTER_DRAIN
sh_ "dumpsys batterystats $PKG" > $OUTD/lr_batterystats.txt
S8_TAG=lr "$PY" qa/s8_verify.py "$TRIP" "$OFF" "$ON" >> $L 2>&1
echo "LONGRUN done $(now)" >> $L
