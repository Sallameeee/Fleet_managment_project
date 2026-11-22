"""Morning-of-demo check. Run:  python qa/demo_morning_check.py
Prints GO or NO-GO with the reason. Uses the demo accounts in qa/out/demo.json."""
import json
import subprocess
import sys
import time
from datetime import datetime

from qa_lib import CAIRO, OUT, ROOT, api

demo = json.loads((OUT / "demo.json").read_text(encoding="utf-8"))
problems = []

for i in range(18):  # wait up to 90 s for the backend
    code, d, _ = api("GET", "/")
    if code == 200:
        break
    time.sleep(5)
else:
    problems.append(f"backend not answering ({code})")

acc = [{"kind": "staff", "label": "demo manager", "login": demo["manager"]["login"], "password": demo["manager"]["password"]},
       {"kind": "staff", "label": "demo driver", "login": demo["driver"]["login"], "password": demo["driver"]["password"]},
       {"kind": "passenger", "label": "demo student", "email": demo["student"]["email"], "password": demo["student"]["password"]}]
(OUT / "smoke_accounts.json").write_text(json.dumps(acc, indent=2), encoding="utf-8")
r = subprocess.run([sys.executable, str(ROOT / "qa" / "smoke.py")], capture_output=True, text=True, encoding="utf-8")
print(r.stdout)
if r.returncode != 0:
    problems.append("smoke failed (see above)")

code, d, _ = api("POST", "/auth/login", body={"login": demo["driver"]["login"], "password": demo["driver"]["password"]})
if code == 200:
    c2, a, _ = api("GET", "/trips/my-assignments", d["access_token"])
    today = datetime.now(CAIRO).date().isoformat()
    items = a if isinstance(a, list) else (a.get("assignments") or a.get("items") or []) if isinstance(a, dict) else []
    todays = [x for x in items if str(x.get("trip_date", ""))[:10] == today]
    if not todays:
        problems.append(f"no demo assignment for today ({today}) for the driver (status {c2})")
    else:
        print(f"today's assignment: {todays[0].get('start_time')}–{todays[0].get('end_time')} on {today}")
else:
    problems.append(f"driver login failed ({code})")

print("\nGO" if not problems else "\nNO-GO: " + "; ".join(problems))
sys.exit(0 if not problems else 1)
