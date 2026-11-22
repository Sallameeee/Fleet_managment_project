"""Read-only check that every object migrations 001-046 create exists in the live DB.

Parses fleet-management/migrations/*.sql for CREATE TABLE / ADD COLUMN / ADD VALUE
and probes them through PostgREST with the service key (SELECT ... LIMIT 0 only;
an enum value is probed with an `eq` filter, which 400s if the value is unknown).
Indexes can't be seen through PostgREST: their names are printed so they can be
checked with one read-only SQL query (see --index-sql).

Usage:  python -I qa/verify_schema.py            (reads fleet-management/.env)
        python -I qa/verify_schema.py --index-sql
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "fleet-management" / "migrations"


def load_env():
    env = {}
    for line in (ROOT / "fleet-management" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["SUPABASE_URL"].rstrip("/")
    for suffix in ("/rest/v1", "/rest"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url, env["SUPABASE_SERVICE_KEY"]


def parse_migrations():
    tables, columns, enum_values, indexes = {}, [], [], []
    for f in sorted(MIG.glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", f.read_text(encoding="utf-8")).lower()
        mig = f.name[:3]
        for m in re.finditer(r"create table(?: if not exists)?\s+(?:public\.)?([a-z_]+)", sql):
            tables.setdefault(m.group(1), mig)
        # ALTER TABLE <t> ... ADD COLUMN <c> (possibly several ADD COLUMN in one statement)
        for stmt in re.finditer(r"alter table(?: if exists)?\s+(?:only\s+)?(?:public\.)?([a-z_]+)(.*?);", sql, re.S):
            t = stmt.group(1)
            for c in re.finditer(r"add column(?: if not exists)?\s+([a-z_]+)", stmt.group(2)):
                columns.append((mig, t, c.group(1)))
            # A later migration may intentionally drop an interim column (e.g. 019 drops 018's).
            for c in re.finditer(r"drop column(?: if exists)?\s+([a-z_]+)", stmt.group(2)):
                columns = [x for x in columns if not (x[1] == t and x[2] == c.group(1))]
        for m in re.finditer(r"alter type\s+(?:public\.)?([a-z_]+)\s+add value(?: if not exists)?\s+'([a-z_]+)'", sql):
            enum_values.append((mig, m.group(1), m.group(2)))
        for m in re.finditer(r"create (unique )?index(?: concurrently)?(?: if not exists)?\s+([a-z0-9_]+)", sql):
            indexes.append((mig, m.group(2), bool(m.group(1))))
    return tables, columns, enum_values, indexes


def probe(base, key, path):
    req = urllib.request.Request(base + "/rest/v1/" + path,
                                 headers={"apikey": key, "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            body = json.loads(body).get("message", body)
        except Exception:
            pass
        return e.code, body[:160]


def main():
    tables, columns, enum_values, indexes = parse_migrations()
    if "--index-sql" in sys.argv:
        names = ",".join("'%s'" % n for _, n, _ in indexes)
        print("select i.name, (p.indexname is not null) as present from unnest(array[%s]) as i(name) "
              "left join pg_indexes p on p.indexname = i.name and p.schemaname = 'public' order by present, i.name;" % names)
        return
    base, key = load_env()
    fails = 0
    print("TABLES (%d)" % len(tables))
    for t, mig in sorted(tables.items(), key=lambda x: x[1]):
        code, msg = probe(base, key, "%s?select=*&limit=0" % t)
        ok = code == 200
        fails += not ok
        print("  %s %-28s mig %s  %s %s" % ("PASS" if ok else "FAIL", t, mig, code, msg))
    print("COLUMNS (%d)" % len(columns))
    col_fail = []
    for mig, t, c in columns:
        code, msg = probe(base, key, "%s?select=%s&limit=0" % (t, c))
        if code != 200:
            col_fail.append((mig, t, c, code, msg))
    fails += len(col_fail)
    print("  PASS %d/%d" % (len(columns) - len(col_fail), len(columns)))
    for f in col_fail:
        print("  FAIL mig %s %s.%s -> %s %s" % f)
    print("ENUM VALUES (%d)" % len(enum_values))
    for mig, typ, val in enum_values:
        # alert_type is the type of alerts.type; an unknown value makes the filter 400.
        code, msg = probe(base, key, "alerts?select=id&type=eq.%s&limit=0" % urllib.parse.quote(val))
        ok = code == 200
        fails += not ok
        print("  %s %s.%s (mig %s) %s %s" % ("PASS" if ok else "FAIL", typ, val, mig, code, msg))
    print("INDEXES (%d, %d unique) - verify with --index-sql" % (len(indexes), sum(u for *_, u in indexes)))
    print("TOTAL FAILS:", fails)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
