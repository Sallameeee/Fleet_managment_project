"""In-memory stand-in for the Supabase/PostgREST client — enough of the query
builder for the tracking pipeline (ingest, lifecycle, progression, decision
log, trip debug, live readers) to run UNCHANGED in tests, with no network and
no production data. Unique keys mirror the real migrations, so idempotency
(re-sent pings, heartbeats, app events, stop visits) is exercised for real.

install() must run BEFORE any backend module is imported: it registers a fake
`database` module whose `supabase` is a FakeClient.
"""

from __future__ import annotations

import copy
import itertools
import sys
import types
from datetime import datetime, timezone

UNIQUE = {
    "location_pings": [("trip_id", "recorded_at")],
    "trip_heartbeats": [("trip_id", "sent_at")],
    "trip_events": [("trip_id", "client_event_id")],
    "stop_visits": [("trip_id", "stop_id")],
}
SERIAL = {"location_pings", "trip_heartbeats", "trip_events"}
DEFAULTS = {
    "trip_events": {"payload": {}, "message": ""},
    "trips": {"progress_version": 0},
    "location_pings": {"is_outlier": False},
}
_ids = itertools.count(1)


class APIError(Exception):
    pass


def _dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and len(v) >= 19 and v[4] == "-" and v[10] in "T ":
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _norm(v):
    d = _dt(v)
    return d if d is not None else v


def _cmp(a, b):
    a, b = _norm(a), _norm(b)
    if a is None or b is None:
        return None
    try:
        return (a > b) - (a < b)
    except TypeError:
        return (str(a) > str(b)) - (str(a) < str(b))


def _eq(a, b):
    na, nb = _norm(a), _norm(b)
    if isinstance(na, datetime) or isinstance(nb, datetime):
        return na == nb
    return str(a) == str(b) if a is not None and b is not None else a is b


class Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op = "select"
        self.cols = "*"
        self.filters = []
        self.orders = []
        self.lim = None
        self.rng = None
        self.payload = None
        self.on_conflict = None
        self.ignore_dups = False

    # builders
    def select(self, cols="*", count=None):
        self.op, self.cols = "select", cols
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self.op, self.payload, self.on_conflict, self.ignore_dups = "upsert", rows, on_conflict, ignore_duplicates
        return self

    def update(self, fields):
        self.op, self.payload = "update", fields
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _f(self, fn):
        self.filters.append(fn)
        return self

    def eq(self, c, v):
        return self._f(lambda r: _eq(r.get(c), v))

    def neq(self, c, v):
        return self._f(lambda r: not _eq(r.get(c), v))

    def in_(self, c, vs):
        vs = list(vs)
        return self._f(lambda r: any(_eq(r.get(c), v) for v in vs))

    def gte(self, c, v):
        return self._f(lambda r: (_cmp(r.get(c), v) or 0) >= 0 and r.get(c) is not None)

    def gt(self, c, v):
        return self._f(lambda r: (_cmp(r.get(c), v) or 0) > 0)

    def lte(self, c, v):
        return self._f(lambda r: r.get(c) is not None and (_cmp(r.get(c), v) or 0) <= 0)

    def lt(self, c, v):
        return self._f(lambda r: r.get(c) is not None and (_cmp(r.get(c), v) or 0) < 0)

    def is_(self, c, v):
        return self._f(lambda r: r.get(c) is None) if str(v) == "null" else self._f(lambda r: r.get(c) == v)

    def or_(self, expr):
        parts = [p.split(".", 2) for p in expr.split(",")]

        def ok(r):
            for col, op, val in parts:
                x = r.get(col)
                if op == "is" and val == "null" and x is None:
                    return True
                if op == "lt" and x is not None and (_cmp(x, val) or 0) < 0:
                    return True
                if op == "eq" and _eq(x, val):
                    return True
            return False
        return self._f(ok)

    def order(self, c, desc=False):
        self.orders.append((c, desc))
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    # execution
    def _rows(self):
        return self.db.tables.setdefault(self.table, [])

    def _match(self):
        return [r for r in self._rows() if all(f(r) for f in self.filters)]

    def _project(self, r):
        if self.cols.strip() == "*":
            return copy.deepcopy(r)
        out = {}
        for c in [c.strip() for c in self.cols.split(",") if c.strip()]:
            if c not in r and self.db.strict_cols.get(self.table) and c not in self.db.strict_cols[self.table]:
                raise APIError(f"column {self.table}.{c} does not exist")
            out[c] = copy.deepcopy(r.get(c))
        return out

    def _conflict_cols(self):
        if self.on_conflict:
            return [tuple(c.strip() for c in self.on_conflict.split(","))]
        return UNIQUE.get(self.table, [])

    def _find_conflict(self, row, keys):
        for k in keys:
            if any(row.get(c) is None for c in k):
                continue
            for r in self._rows():
                if all(_eq(r.get(c), row.get(c)) for c in k):
                    return r
        return None

    def _new(self, row):
        r = {**DEFAULTS.get(self.table, {}), **copy.deepcopy(row)}
        if "id" not in r or r["id"] is None:
            r["id"] = next(_ids) if self.table in SERIAL else f"00000000-0000-4000-8000-{next(_ids):012d}"
        r.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        return r

    def execute(self):
        self.db.calls += 1
        if self.op == "select":
            rows = self._match()
            for c, desc in reversed(self.orders):
                rows.sort(key=lambda r: (r.get(c) is None, _norm(r.get(c)) if r.get(c) is not None else 0), reverse=desc)
            if self.rng:
                rows = rows[self.rng[0]:self.rng[1] + 1]
            if self.lim is not None:
                rows = rows[: self.lim]
            return Result([self._project(r) for r in rows])
        if self.op in ("insert", "upsert"):
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            keys = self._conflict_cols() if self.op == "upsert" else UNIQUE.get(self.table, [])
            for row in payload:
                hit = self._find_conflict(row, keys)
                if hit is not None:
                    if self.op == "insert":
                        raise APIError(f"duplicate key value violates unique constraint uq_{self.table}")
                    if self.ignore_dups:
                        continue
                    hit.update(copy.deepcopy(row))
                    out.append(copy.deepcopy(hit))
                    continue
                r = self._new(row)
                self._rows().append(r)
                out.append(copy.deepcopy(r))
            return Result(out)
        if self.op == "update":
            out = []
            for r in self._match():
                r.update(copy.deepcopy(self.payload))
                out.append(copy.deepcopy(r))
            return Result(out)
        if self.op == "delete":
            gone = self._match()
            self.db.tables[self.table] = [r for r in self._rows() if r not in gone]
            return Result(copy.deepcopy(gone))
        raise APIError(self.op)


class RPC:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        if self.name == "purge_trip_events":
            from datetime import timedelta
            cut = datetime.now(timezone.utc) - timedelta(days=int(self.params.get("days") or 30))
            rows = self.db.tables.get("trip_events", [])
            keep = [r for r in rows if _dt(r["created_at"]) >= cut]
            self.db.tables["trip_events"] = keep
            return Result(len(rows) - len(keep))
        raise APIError(f"unknown rpc {self.name}")


class FakeClient:
    def __init__(self):
        self.tables: dict = {}
        self.strict_cols: dict = {}
        self.calls = 0

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params=None):
        return RPC(self, name, params or {})

    def reset(self):
        self.tables.clear()
        self.calls = 0


def install() -> FakeClient:
    if "database" in sys.modules and isinstance(getattr(sys.modules["database"], "supabase", None), FakeClient):
        return sys.modules["database"].supabase
    client = FakeClient()
    mod = types.ModuleType("database")
    mod.supabase = client
    mod.SUPABASE_URL = "https://fake.supabase.local"

    def is_transient(exc):
        return False

    def retry_transient(fn, tries=3, base_delay=0.0):
        return fn()

    mod.is_transient = is_transient
    mod.retry_transient = retry_transient
    sys.modules["database"] = mod
    return client
