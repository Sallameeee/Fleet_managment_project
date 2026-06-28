from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import (
    admin,
    alert_rules,
    alerts,
    assignments,
    auth,
    dashboard,
    drivers,
    finance,
    live,
    organizations,
    reports,
    routes,
    tracking,
    trips,
    users,
    vehicles,
)

app = FastAPI(title="routemind-fleet")

# CORS: the admin dashboard (Next.js dev server) is a different origin, so the
# browser needs these headers to allow its fetch() calls. localhost and
# 127.0.0.1 are distinct origins to the browser, so allow both on port 3000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(drivers.router)
app.include_router(vehicles.router)
app.include_router(users.router)
app.include_router(routes.router)
app.include_router(assignments.router)
app.include_router(trips.router)
app.include_router(alert_rules.router)
app.include_router(alerts.router)
app.include_router(reports.router)
app.include_router(tracking.router)
app.include_router(finance.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(live.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "routemind-fleet"}
