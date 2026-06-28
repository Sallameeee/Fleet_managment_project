// Translation dictionary — ONE place for all UI strings.
//
// Structure: every key maps to { en, ar }. Add a key here, then use it in a
// component via `const { t } = useT(); t("your.key")`. Keys are namespaced by
// area (common.*, nav.*, alerts.*, …) so related strings stay together. Data
// values (driver names, etc.) are NOT translated — only UI chrome.
//
// To add/edit wording: change the en/ar text below. To add a new string: add a
// new key and reference it with t("...").

export type Lang = "en" | "ar";

export const dict: Record<string, { en: string; ar: string }> = {
  // --- common ---------------------------------------------------------------
  "common.save": { en: "Save", ar: "حفظ" },
  "common.cancel": { en: "Cancel", ar: "إلغاء" },
  "common.create": { en: "Create", ar: "إنشاء" },
  "common.delete": { en: "Delete", ar: "حذف" },
  "common.edit": { en: "Edit", ar: "تعديل" },
  "common.done": { en: "Done", ar: "تم" },
  "common.retry": { en: "Retry", ar: "إعادة المحاولة" },
  "common.loading": { en: "Loading…", ar: "جارٍ التحميل…" },
  "common.active": { en: "active", ar: "نشط" },
  "common.inactive": { en: "inactive", ar: "غير نشط" },
  "common.suspended": { en: "suspended", ar: "موقوف" },
  "common.expired": { en: "expired", ar: "منتهٍ" },
  "common.online": { en: "online", ar: "متصل" },
  "common.offline": { en: "offline", ar: "غير متصل" },
  "common.yes": { en: "yes", ar: "نعم" },
  "common.no": { en: "no", ar: "لا" },
  "common.status": { en: "Status", ar: "الحالة" },
  "common.actions": { en: "Actions", ar: "إجراءات" },
  "common.name": { en: "Name", ar: "الاسم" },
  "common.email": { en: "Email", ar: "البريد الإلكتروني" },
  "common.phone": { en: "Phone", ar: "الهاتف" },
  "common.username": { en: "Username", ar: "اسم المستخدم" },
  "common.password": { en: "Password", ar: "كلمة المرور" },
  "common.date": { en: "Date", ar: "التاريخ" },
  "common.type": { en: "Type", ar: "النوع" },
  "common.detail": { en: "Detail", ar: "التفاصيل" },
  "common.driver": { en: "Driver", ar: "السائق" },
  "common.vehicle": { en: "Vehicle", ar: "المركبة" },
  "common.route": { en: "Route", ar: "المسار" },
  "common.organization": { en: "Organization", ar: "المؤسسة" },
  "common.required": { en: "required", ar: "مطلوب" },
  "common.role": { en: "Role", ar: "الدور" },
  "common.address": { en: "Address", ar: "العنوان" },
  "common.maxDevices": { en: "Max devices", ar: "أقصى عدد للأجهزة" },
  "common.activeHdr": { en: "Active", ar: "نشط" },
  "common.none": { en: "No records yet.", ar: "لا توجد سجلات بعد." },
  "common.failed": { en: "Action failed.", ar: "فشل الإجراء." },
  "common.loadFailed": { en: "Failed to load.", ar: "تعذّر التحميل." },
  "common.createAnother": { en: "Create another", ar: "إنشاء آخر" },
  "common.from": { en: "From", ar: "من" },
  "common.to": { en: "To", ar: "إلى" },

  // --- org-create / org actions (super-admin) -------------------------------
  "orgs.ownerUsername": { en: "Owner username", ar: "اسم مستخدم المالك" },
  "orgs.ownerPassword": { en: "Owner password", ar: "كلمة مرور المالك" },
  "orgs.subscriptionExpiry": { en: "Subscription expiry", ar: "انتهاء الاشتراك" },
  "orgs.created": { en: "Organization created. Owner login to hand to the client:", ar: "تم إنشاء المؤسسة. بيانات دخول المالك لتسليمها للعميل:" },
  "orgs.viewDrivers": { en: "View drivers", ar: "عرض السائقين" },
  "orgs.viewUsers": { en: "View users", ar: "عرض المستخدمين" },
  "orgs.loginAs": { en: "Log in as this org", ar: "الدخول كهذه المؤسسة" },
  "orgs.deleteOrg": { en: "Delete org", ar: "حذف المؤسسة" },
  "orgs.deleteOrgTitle": { en: "Delete organization", ar: "حذف المؤسسة" },
  "orgs.deleteWarnPre": { en: "This permanently deletes", ar: "سيؤدي هذا إلى حذف نهائي لـ" },
  "orgs.deleteWarnPost": { en: "— all its users (and their logins), vehicles, routes, trips, assignments, alerts, and payments. This cannot be undone.", ar: "— جميع مستخدميها (وبيانات دخولهم) والمركبات والمسارات والرحلات والتكليفات والتنبيهات والمدفوعات. لا يمكن التراجع عن ذلك." },
  "orgs.deleteTypePre": { en: "Type", ar: "اكتب" },
  "orgs.deleteTypePost": { en: "to confirm", ar: "للتأكيد" },
  "orgs.deletePermanently": { en: "Delete permanently", ar: "حذف نهائي" },
  "orgs.deleting": { en: "Deleting…", ar: "جارٍ الحذف…" },
  "orgs.noneInOrg": { en: "None in this organization.", ar: "لا يوجد في هذه المؤسسة." },

  // --- org detail (super-admin) --------------------------------------------
  "orgsd.editSubscription": { en: "Edit subscription", ar: "تعديل الاشتراك" },
  "orgsd.driversVehicles": { en: "Drivers / Vehicles", ar: "السائقون / المركبات" },
  "orgsd.bannerPre": { en: "This organization is", ar: "هذه المؤسسة" },
  "orgsd.bannerPost": { en: "— its users cannot sign in.", ar: "— لا يمكن لمستخدميها تسجيل الدخول." },
  "orgsd.usersFrozen": { en: "Its users will be blocked from signing in.", ar: "سيُمنع مستخدموها من تسجيل الدخول." },

  // --- platform users (super-admin) ----------------------------------------
  "users.newPlatformUser": { en: "New platform user", ar: "مستخدم منصّة جديد" },
  "users.deleteConfirmPre": { en: "Delete platform user", ar: "حذف مستخدم المنصّة" },
  "users.deleteConfirmPost": { en: "? This removes their login.", ar: "؟ سيؤدي هذا إلى إزالة تسجيل دخوله." },

  // --- assignments extras ---------------------------------------------------
  "assign.shiftLabel": { en: "Shift label", ar: "اسم الوردية" },
  "assign.startTime": { en: "Start time", ar: "وقت البدء" },

  // --- vehicles extras ------------------------------------------------------
  "vehicles.plateNumber": { en: "Plate number", ar: "رقم اللوحة" },

  // --- routes extras --------------------------------------------------------
  "routes.lat": { en: "Lat", ar: "خط العرض" },
  "routes.lng": { en: "Lng", ar: "خط الطول" },
  "routes.dwell": { en: "Dwell", ar: "مدة التوقف" },
  "routes.noStops": { en: "No stops.", ar: "لا توجد محطات." },

  // --- reports extras -------------------------------------------------------
  "reports.kilometers": { en: "Kilometers — by vehicle / by driver", ar: "الكيلومترات — حسب المركبة / السائق" },
  "reports.speed": { en: "Speed", ar: "السرعة" },
  "reports.kmShort": { en: "Kilometers", ar: "الكيلومترات" },
  "reports.bus": { en: "Bus", ar: "الحافلة" },
  "reports.maxKmh": { en: "Max km/h", ar: "أقصى كم/س" },
  "reports.avgKmh": { en: "Avg km/h", ar: "متوسط كم/س" },
  "reports.speedingAlerts": { en: "Speeding alerts", ar: "تنبيهات السرعة" },
  "reports.pickType": { en: "Pick at least one report type.", ar: "اختر نوع تقرير واحدًا على الأقل." },
  "reports.customNeedsDates": { en: "Custom range needs both dates.", ar: "النطاق المخصص يتطلب التاريخين." },

  // --- full view extras -----------------------------------------------------
  "full.updated": { en: "updated", ar: "محدّث منذ" },
  "full.secShort": { en: "s", ar: "ث" },
  "full.minShort": { en: "m", ar: "د" },
  "full.hrShort": { en: "h", ar: "س" },
  "full.noPings": { en: "no pings yet", ar: "لا توجد إشارات بعد" },

  // --- settings extras ------------------------------------------------------
  "settings.alwaysOnHelp": { en: "The public link shows live position whenever a trip is running.", ar: "يعرض الرابط العام الموقع المباشر عند وجود رحلة جارية." },
  "settings.windowHelp": { en: "Outside this window the public link shows “tracking resumes at <start>” instead of a position.", ar: "خارج هذه الفترة يعرض الرابط العام «يُستأنف التتبّع في <البداية>» بدلًا من الموقع." },

  // --- nav ------------------------------------------------------------------
  "nav.dashboard": { en: "Dashboard", ar: "الرئيسية" },
  "nav.fullView": { en: "Full View", ar: "العرض الكامل" },
  "nav.drivers": { en: "Drivers", ar: "السائقون" },
  "nav.vehicles": { en: "Vehicles", ar: "المركبات" },
  "nav.routes": { en: "Routes", ar: "المسارات" },
  "nav.assignments": { en: "Assignments", ar: "التكليفات" },
  "nav.trips": { en: "Trips", ar: "الرحلات" },
  "nav.alerts": { en: "Alerts", ar: "التنبيهات" },
  "nav.reports": { en: "Reports", ar: "التقارير" },
  "nav.settings": { en: "Settings", ar: "الإعدادات" },
  "nav.organizations": { en: "Organizations", ar: "المؤسسات" },
  "nav.finance": { en: "Finance", ar: "المالية" },
  "nav.users": { en: "Users", ar: "المستخدمون" },

  // --- header ---------------------------------------------------------------
  "header.signedInAs": { en: "Signed in as", ar: "مسجّل الدخول باسم" },
  "header.signOut": { en: "Sign out", ar: "تسجيل الخروج" },
  "header.viewingAs": { en: "Viewing as", ar: "العرض باسم" },
  "header.impersonation": { en: "impersonation", ar: "انتحال" },
  "header.exitImpersonation": { en: "Exit impersonation", ar: "إنهاء الانتحال" },

  // --- login ----------------------------------------------------------------
  "login.adminTitle": { en: "Fleet Admin", ar: "إدارة الأسطول" },
  "login.adminSubtitle": { en: "Super admin panel", ar: "لوحة المشرف العام" },
  "login.managerTitle": { en: "Fleet Manager", ar: "مدير الأسطول" },
  "login.managerSubtitle": { en: "Organization sign in", ar: "تسجيل دخول المؤسسة" },
  "login.signIn": { en: "Sign in", ar: "تسجيل الدخول" },
  "login.authorizedOnly": { en: "Authorized platform operators only.", ar: "لمشغّلي المنصّة المصرّح لهم فقط." },
  "login.orgHint": { en: "Sign in with your organization username.", ar: "سجّل الدخول باسم المستخدم الخاص بمؤسستك." },

  // --- alerts ---------------------------------------------------------------
  "alerts.subtitle": { en: "Notifications and the rules that drive them", ar: "الإشعارات والقواعد التي تُنشئها" },
  "alerts.tabAlerts": { en: "Alerts", ar: "التنبيهات" },
  "alerts.tabRules": { en: "Rules", ar: "القواعد" },
  "alerts.allTypes": { en: "All types", ar: "كل الأنواع" },
  "alerts.readAndUnread": { en: "Read & unread", ar: "المقروء وغير المقروء" },
  "alerts.unread": { en: "Unread", ar: "غير مقروء" },
  "alerts.read": { en: "Read", ar: "مقروء" },
  "alerts.markRead": { en: "Mark read", ar: "تعليم كمقروء" },
  "alerts.when": { en: "When", ar: "الوقت" },
  "alerts.noAlerts": { en: "No alerts.", ar: "لا توجد تنبيهات." },
  "alerts.rulesTitle": { en: "Alert rules", ar: "قواعد التنبيه" },
  "alerts.newRule": { en: "New rule", ar: "قاعدة جديدة" },
  "alerts.threshold": { en: "Threshold", ar: "الحد" },
  "alerts.target": { en: "Target", ar: "النطاق" },
  "alerts.appliesTo": { en: "Applies to", ar: "ينطبق على" },
  "alerts.targetAll": { en: "All", ar: "الكل" },
  "alerts.targetVehicles": { en: "Specific vehicles", ar: "مركبات محددة" },
  "alerts.targetDrivers": { en: "Specific drivers", ar: "سائقون محددون" },
  "alerts.notifyVia": { en: "Notify via", ar: "التنبيه عبر" },
  "alerts.panel": { en: "Panel", ar: "اللوحة" },
  "alerts.comingSoon": { en: "coming soon", ar: "قريبًا" },
  "alerts.noRules": { en: "No rules.", ar: "لا توجد قواعد." },
  "alerts.shortStopNote": { en: "short_stop uses each stop's required dwell time — no threshold needed.", ar: "يستخدم short_stop مدة التوقف المطلوبة لكل محطة — لا حاجة لحد." },
  "alerts.ruleOff": { en: "off", ar: "متوقف" },

  // --- dashboard summary ----------------------------------------------------
  "summary.totalDrivers": { en: "Total drivers", ar: "إجمالي السائقين" },
  "summary.online": { en: "Online", ar: "متصل" },
  "summary.offline": { en: "Offline", ar: "غير متصل" },
  "summary.workingNow": { en: "Working now", ar: "يعمل الآن" },
  "summary.topDriver": { en: "Top driver this month", ar: "أفضل سائق هذا الشهر" },
  "summary.actualKm": { en: "Actual km", ar: "الكيلومترات الفعلية" },
  "summary.trips": { en: "Trips", ar: "الرحلات" },
  "summary.score": { en: "Score", ar: "النتيجة" },
  "summary.liveAlerts": { en: "Live alerts", ar: "تنبيهات مباشرة" },
  "summary.noTripsMonth": { en: "No trips recorded this month.", ar: "لا توجد رحلات مسجّلة هذا الشهر." },

  // --- full view ------------------------------------------------------------
  "full.activeDrivers": { en: "Active drivers", ar: "السائقون النشطون" },
  "full.mapPlaceholder": { en: "Live map — Mapbox integration coming here", ar: "الخريطة المباشرة — تكامل Mapbox قادم هنا" },
  "full.mapSub": { en: "Drivers will render as points labeled with their current vehicle.", ar: "ستظهر السائقون كنقاط موسومة بمركباتهم الحالية." },
  "full.history": { en: "Driver path history will appear here (with the map)", ar: "سيظهر سجلّ مسار السائق هنا (مع الخريطة)" },
  "full.noActive": { en: "No drivers on an active trip right now.", ar: "لا يوجد سائقون في رحلة نشطة الآن." },
  "full.autoRefresh": { en: "auto-refresh 15s", ar: "تحديث تلقائي كل ١٥ ثانية" },
  "full.noPosition": { en: "no position yet", ar: "لا يوجد موقع بعد" },

  // --- settings -------------------------------------------------------------
  "settings.subtitle": { en: "Organization configuration", ar: "إعدادات المؤسسة" },
  "settings.trackingHours": { en: "Public tracking hours", ar: "ساعات التتبّع العامة" },
  "settings.current": { en: "Current", ar: "الحالي" },
  "settings.alwaysOnLabel": { en: "Always on (tracking live whenever a trip is active)", ar: "دائمًا مفعّل (تتبّع مباشر عند وجود رحلة نشطة)" },
  "settings.start": { en: "Start", ar: "البداية" },
  "settings.end": { en: "End", ar: "النهاية" },
  "settings.saved": { en: "Saved.", ar: "تم الحفظ." },

  // --- drivers / vehicles ---------------------------------------------------
  "drivers.newDriver": { en: "New driver", ar: "سائق جديد" },
  "drivers.currentVehicle": { en: "Current vehicle", ar: "المركبة الحالية" },
  "drivers.appLogin": { en: "Driver created. Their app login:", ar: "تم إنشاء السائق. بيانات دخول التطبيق:" },
  "drivers.addAnother": { en: "Add another", ar: "إضافة آخر" },
  "vehicles.newVehicle": { en: "New vehicle", ar: "مركبة جديدة" },
  "vehicles.busNumber": { en: "Bus number", ar: "رقم الحافلة" },
  "vehicles.plate": { en: "Plate", ar: "اللوحة" },
  "vehicles.trackingLink": { en: "Passenger tracking link", ar: "رابط تتبّع الركّاب" },
  "vehicles.copy": { en: "Copy", ar: "نسخ" },
  "vehicles.copied": { en: "Copied", ar: "تم النسخ" },

  // --- routes ---------------------------------------------------------------
  "routes.newRoute": { en: "New route", ar: "مسار جديد" },
  "routes.totalKm": { en: "Total km", ar: "إجمالي الكيلومترات" },
  "routes.estMinutes": { en: "Est. minutes", ar: "الدقائق التقديرية" },
  "routes.stops": { en: "Stops", ar: "المحطات" },
  "routes.mapPickerNote": { en: "map picker coming later — enter lat/lng manually", ar: "منتقي الخريطة لاحقًا — أدخل الإحداثيات يدويًا" },
  "routes.addStop": { en: "Add stop", ar: "إضافة محطة" },

  // --- assignments ----------------------------------------------------------
  "assign.newAssignment": { en: "New assignment", ar: "تكليف جديد" },
  "assign.shift": { en: "Shift", ar: "الوردية" },
  "assign.start": { en: "Start", ar: "البداية" },
  "assign.tripDate": { en: "Trip date", ar: "تاريخ الرحلة" },
  "assign.assign": { en: "Assign", ar: "تكليف" },
  "assign.select": { en: "Select…", ar: "اختر…" },
  "assign.clear": { en: "Clear", ar: "مسح" },
  "assign.none": { en: "No assignments.", ar: "لا توجد تكليفات." },

  // --- reports --------------------------------------------------------------
  "reports.subtitle": { en: "Combine report types over a period", ar: "ادمج أنواع التقارير على مدى فترة" },
  "reports.generate": { en: "Generate", ar: "إنشاء" },
  "reports.downloadPdf": { en: "Download PDF", ar: "تنزيل PDF" },
  "reports.period": { en: "Period", ar: "الفترة" },
  "reports.today": { en: "Today", ar: "اليوم" },
  "reports.week": { en: "This week", ar: "هذا الأسبوع" },
  "reports.month": { en: "This month", ar: "هذا الشهر" },
  "reports.custom": { en: "Custom", ar: "مخصص" },
  "reports.from": { en: "From", ar: "من" },
  "reports.to": { en: "To", ar: "إلى" },
  "reports.plannedKm": { en: "Planned km", ar: "كم مخطط" },
  "reports.actualKm": { en: "Actual km", ar: "كم فعلي" },
  "reports.diff": { en: "Diff", ar: "الفرق" },

  // --- super-admin: organizations ------------------------------------------
  "orgs.total": { en: "total", ar: "الإجمالي" },
  "orgs.newOrg": { en: "New organization", ar: "مؤسسة جديدة" },
  "orgs.plan": { en: "Plan", ar: "الخطة" },
  "orgs.monthlyFee": { en: "Monthly fee", ar: "الرسوم الشهرية" },
  "orgs.expiry": { en: "Expiry", ar: "الانتهاء" },
  "orgs.suspend": { en: "Suspend", ar: "تعليق" },
  "orgs.activate": { en: "Activate", ar: "تفعيل" },
  "orgs.login": { en: "Login", ar: "دخول" },

  // --- super-admin: finance -------------------------------------------------
  "finance.subtitle": { en: "Subscriptions & collections across the platform", ar: "الاشتراكات والتحصيلات عبر المنصّة" },
  "finance.totalExpected": { en: "Total expected", ar: "إجمالي المتوقع" },
  "finance.totalCollected": { en: "Total collected", ar: "إجمالي المحصّل" },
  "finance.totalOutstanding": { en: "Total outstanding", ar: "إجمالي المستحق" },
  "finance.expected": { en: "Expected", ar: "المتوقع" },
  "finance.collected": { en: "Collected", ar: "المحصّل" },
  "finance.outstanding": { en: "Outstanding", ar: "المستحق" },

  // --- super-admin: users ---------------------------------------------------
  "users.subtitle": { en: "Platform staff who help run the panel", ar: "موظفو المنصّة الذين يساعدون في تشغيل اللوحة" },
  "users.newUser": { en: "New user", ar: "مستخدم جديد" },
  "users.permissions": { en: "Permissions", ar: "الصلاحيات" },
};
