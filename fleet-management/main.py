from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import (
    admin,
    alert_rules,
    alerts,
    assignments,
    attendance,
    auth,
    bus_drivers,
    capacity,
    centers,
    change_requests,
    dashboard,
    driver_groups,
    drivers,
    finance,
    history,
    live,
    notifications,
    organizations,
    parent_reports,
    passenger,
    passengers,
    profile_requests,
    report_schedules,
    reports,
    routes,
    school,
    tracking,
    trips,
    users,
    vehicles,
)

app = FastAPI(title="routemind-fleet")

# CORS: permissive for now — the dashboard (web) and the driver/passenger mobile
# apps all call this API from different origins. Auth is via a Bearer token in the
# Authorization header (not cookies), so we don't need credentialed CORS; that's
# why allow_credentials is False, which lets us safely use the "*" wildcard
# (browsers reject "*" together with credentials). Tighten allow_origins to the
# real domains once they're known.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(drivers.router)
app.include_router(bus_drivers.router)
app.include_router(driver_groups.router)
app.include_router(centers.router)
app.include_router(vehicles.router)
app.include_router(users.router)
app.include_router(routes.router)
app.include_router(assignments.router)
app.include_router(trips.router)
app.include_router(alert_rules.router)
app.include_router(alerts.router)
app.include_router(reports.router)
app.include_router(report_schedules.router)
app.include_router(tracking.router)
app.include_router(finance.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(live.router)
app.include_router(history.router)
app.include_router(passengers.router)
app.include_router(passenger.router)
app.include_router(attendance.router)
app.include_router(capacity.router)
app.include_router(change_requests.router)
app.include_router(school.router)
app.include_router(notifications.router)
app.include_router(profile_requests.router)
app.include_router(parent_reports.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "routemind-fleet"}
