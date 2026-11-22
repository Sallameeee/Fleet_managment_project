#!/usr/bin/env bash
# S8b (fixed APK): End Trip pressed OFFLINE on Nox, then delivered later.
#   MODE=foreground  app stays open; the server auto-closes the silent trip
#                    (in-process sweeper, clock +40 min) before the network
#                    returns; the app's 20 s retry delivers fixes + End.
#   MODE=killed      app force-stopped right after the offline End; network
#                    returns while it is dead; delivery happens on relaunch.
# Usage: TRIP=<id> TAG=<tag> MODE=foreground|killed bash qa/nox_s8b2.sh
A="/d/Program Files/Nox/bin/nox_adb.exe"; S=127.0.0.1:62001
OUTD=/d/Fleet_managment_project/qa/out; L=$OUTD/${TAG}_log.txt
PY="/d/Fleet_managment_project/fleet-management/venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8
cd /d/Fleet_managment_project
PKG=com.fleet.fleet_driver_app
DB=/data/data/$PKG/databases/ping_buffer.db
PREFS=/data/data/$PKG/shared_prefs/FlutterSharedPreferences.xml
SP=${SHOTS:-$OUTD}  # screenshots (qa/out is gitignored)
sh_() { timeout 30 "$A" -s $S shell "$1" | tr -d '\r'; }
q() { sh_ "sqlite3 $DB \"$1\""; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
pending() { sh_ "cat $PREFS" | grep -o 'pending_trip_end[^<]*' | sed 's/&quot;/"/g'; }
shot() { timeout 30 "$A" -s $S exec-out screencap -p > "$SP/${TAG}_$1.png"; }
srv() { "$PY" -c "
import sys; sys.path.insert(0,'qa')
from qa_lib import sb, sb_count
t=sb('GET','trips?select=status,end_reason,ended_at&id=eq.$TRIP')[0]
n=sb_count('location_pings?select=id&trip_id=eq.$TRIP')
al=[a['type'] for a in sb('GET','alerts?select=type&trip_id=eq.$TRIP&type=in.(connection_lost,connection_restored,trip_ended)&order=occurred_at')]
print(f\"server: status={t['status']} end_reason={t['end_reason']} ended_at={t['ended_at']} pings={n} lifecycle={al}\")"; }

echo "S8b $MODE trip=$TRIP start $(now)" > $L
sleep 60
echo "ONLINE_BASELINE $(now) buf=$(q 'select count(*) from pings') | $(srv)" >> $L
sh_ "svc wifi disable; svc data disable"; echo "OFF $(now)" >> $L
sleep 180
echo "BEFORE_END $(now) buf_pings=$(q "select count(*) from pings where trip_id='$TRIP'") buf_hb=$(q "select count(*) from heartbeats where trip_id='$TRIP'")" >> $L
# END TRIP is the last item of the list: scrolled to the very bottom its centre
# is always at y~806 in this 1600x900 layout (whatever the stop card's height).
sh_ "input swipe 800 750 800 100 250"; sh_ "input swipe 800 750 800 100 250"; sleep 1
shot before_end_tap
sh_ "input tap 800 806"; echo "END_TAP $(now)" >> $L
sleep 40
shot after_end_offline
echo "AFTER_END_OFFLINE $(now) buf_pings=$(q "select count(*) from pings where trip_id='$TRIP'") buf_hb=$(q "select count(*) from heartbeats where trip_id='$TRIP'") svc_fg=$(sh_ "dumpsys activity services $PKG | grep -c isForeground=true") pending=[$(pending)] | $(srv)" >> $L
q "select recorded_at from pings where trip_id='$TRIP' order by recorded_at" > $OUTD/${TAG}_buffer_pings.txt
END_MS=$(pending | grep -o '"endedAtMs":[0-9]*' | grep -o '[0-9]*$')
echo "SNAPSHOT $(now) buffered_pings=$(wc -l < $OUTD/${TAG}_buffer_pings.txt) end_ms=$END_MS" >> $L
if [ -z "$END_MS" ]; then
  sh_ "svc wifi enable; svc data enable"
  echo "END_TAP_MISSED: no pending end on the phone — network restored, run aborted $(now)" >> $L
  echo "S8b $MODE done (aborted)" >> $L
  exit 1
fi

if [ "$MODE" = "killed" ]; then
  sh_ "am force-stop $PKG"; echo "KILLED $(now) pid=[$(sh_ "pidof $PKG")]" >> $L
else
  # the phone stays offline long enough for the server to auto-close the trip
  # (run from fleet-management/ so database.py's load_dotenv() finds its .env)
  (cd fleet-management && "$PY" -c "
import os, sys; os.environ['LIFECYCLE_SWEEP']='0'
sys.path.insert(0,'../qa'); sys.path.insert(0,'.')
from datetime import timedelta
from qa_lib import utc_now
import trip_lifecycle
print('sweep +40 min:', trip_lifecycle.sweep_once(only_trip_ids={'$TRIP'}, now=utc_now()+timedelta(minutes=40)))") >> $L 2>&1
  echo "AUTO_CLOSED? $(now) | $(srv)" >> $L
fi

sh_ "svc wifi enable; svc data enable"; T_ON=$(date +%s); echo "ON $(now)" >> $L
if [ "$MODE" = "killed" ]; then
  sleep 60
  echo "ONLINE_APP_DEAD $(now) buf_pings=$(q "select count(*) from pings where trip_id='$TRIP'") pending=[$(pending)] | $(srv)" >> $L
  sh_ "monkey -p $PKG -c android.intent.category.LAUNCHER 1" > /dev/null; T_ON=$(date +%s); echo "RELAUNCHED $(now)" >> $L
fi
for i in $(seq 1 60); do
  sleep 3
  b=$(q "select count(*) from pings where trip_id='$TRIP'"); p=$(pending)
  if [ "$b" = "0" ] && [ -z "$p" ]; then break; fi
done
echo "DELIVERED $(now) after $(( $(date +%s) - T_ON ))s buf_pings=$(q "select count(*) from pings where trip_id='$TRIP'") buf_hb=$(q "select count(*) from heartbeats where trip_id='$TRIP'") pending=[$(pending)]" >> $L
sleep 5
shot after_delivery
echo "AFTER $(now) | $(srv)" >> $L
"$PY" qa/s8b_verify.py "$TAG" "$TRIP" "$END_MS" >> $L 2>&1
echo "S8b $MODE done $(now)" >> $L
