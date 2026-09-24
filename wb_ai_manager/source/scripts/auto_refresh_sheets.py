#!/usr/bin/env python3
from wb_control_center.auto_sheets import AutoSheets
from wb_control_center.config import Settings
from wb_control_center.portfolio import PortfolioService

settings=Settings()
portfolio=PortfolioService(settings)
if settings.google_sheets_bridge_url and settings.google_sheets_bridge_key:
    try:
        snap=portfolio.refresh()
        print("AUTO_SHEETS_OK apps_script_bridge", snap.get("generated_at"))
    except Exception as exc:
        print("AUTO_SHEETS_BRIDGE_ERROR", exc)
else:
    a=AutoSheets(); payload=a.refresh_core_via_browser()
    if not payload.get("sources"):
        print("AUTO_SHEETS_NO_DATA")
        print("Если открылся Google Login — войди один раз и запусти команду снова.")
    else:
        snap=portfolio.merge_payload(payload,"auto_browser_sheets")
        print("AUTO_SHEETS_OK", ",".join(payload.get("exported",[])), snap.get("generated_at"))
