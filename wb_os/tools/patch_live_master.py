#!/usr/bin/env python3
import pathlib
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_live_master.py <apps-script-source-root>")

root = pathlib.Path(sys.argv[1]).resolve()

files = []
for ext in ("*.gs", "*.js"):
    files.extend(root.rglob(ext))

candidates = []
for f in files:
    try:
        text = f.read_text(encoding="utf-8")
    except Exception:
        continue

    has_master = "function runFinalAutomationCycle_" in text
    has_k2 = (
        "/* 1. K2 */" in text
        or "/* 1. K2 · WB OS delta engine */" in text
        or "k2EvolutionFetchAndApply_()" in text
    )
    has_iv = "/* 2. Иваново */" in text
    has_refresh = "refreshAutomationStatusSheet_();" in text

    if has_master and has_k2 and has_iv and has_refresh:
        candidates.append((f, text))

if len(candidates) != 1:
    names = [str(f.relative_to(root)) for f, _ in candidates]
    raise SystemExit(
        "PATCH_FAIL: expected exactly one master source; "
        f"found={len(candidates)} files={names}"
    )

path, text = candidates[0]

start = text.index("function runFinalAutomationCycle_")
end_marker = "\nfunction shouldRunByProperty_"
end = text.find(end_marker, start)
if end == -1:
    raise SystemExit("PATCH_FAIL: cannot bound runFinalAutomationCycle_")

before = text[:start]
func = text[start:end]
after = text[end:]

# Step 1: migrate the K2 block only when it is still legacy.
if "k2EvolutionFetchAndApply_()" not in func:
    pattern = re.compile(
        r"\n\s*/\* 1\. K2 \*/.*?\n\s*/\* 2\. Иваново \*/",
        re.S,
    )

    replacement = r'''
    /* 1. K2 · WB OS delta engine */
    if (
      forceAll ||
      historyDue ||
      !String(
        PropertiesService
          .getScriptProperties()
          .getProperty('K2_EV_LAST_RUN_AT') || ''
      ).trim() ||
      !String(
        PropertiesService
          .getScriptProperties()
          .getProperty('K2_EV_CUTOVER_DONE') || ''
      ).trim() ||
      shouldRunByProperty_(
        'K2_EV_LAST_RUN_AT',
        MASTER_AUTOMATION_CFG.K2_EVERY_MINUTES
      )
    ) {
      try {
        var k2Result = k2EvolutionFetchAndApply_();
        k2Updated = Boolean(k2Result && k2Result.changed);

        Logger.log(
          'Master: K2 проверен, позиций: ' +
          (k2Result ? k2Result.itemCount : 0) +
          '; business-state changed=' + k2Updated
        );
      } catch (error) {
        cycleErrors.push('K2: ' + error.message);
        Logger.log(error.stack || error.message);

        try {
          k2EvolutionRecordFailure_(error);
        } catch (diagError) {
          Logger.log('K2 Evolution diagnostics: ' + diagError.message);
        }

        if (getMasterConfigurationState_().telegramReady) {
          notifyK2ApiError_(error);
        }
      }
    }

    /* 2. Иваново */'''

    func, count = pattern.subn(replacement, func, count=1)
    if count != 1:
        raise SystemExit(f"PATCH_FAIL: K2 block replacements={count}")

# Step 1b: guarantee one Evolution run during migration even while a legacy
# K2 scheduling is based on the last real Evolution API run, not the health heartbeat.
if "K2_EV_LAST_RUN_AT" not in func:
    old_condition = (
        "if (forceAll || historyDue || "
        "shouldRunByProperty_('K2_LAST_SUCCESS_AT', "
        "MASTER_AUTOMATION_CFG.K2_EVERY_MINUTES)) {"
    )

    new_condition = """if (
      forceAll ||
      historyDue ||
      !String(
        PropertiesService
          .getScriptProperties()
          .getProperty('K2_EV_LAST_RUN_AT') || ''
      ).trim() ||
      !String(
        PropertiesService
          .getScriptProperties()
          .getProperty('K2_EV_CUTOVER_DONE') || ''
      ).trim() ||
      shouldRunByProperty_(
        'K2_EV_LAST_RUN_AT',
        MASTER_AUTOMATION_CFG.K2_EVERY_MINUTES
      )
    ) {"""

    if old_condition not in func:
        raise SystemExit(
            "PATCH_FAIL: cannot make K2 Evolution migration self-starting"
        )

    func = func.replace(
        old_condition,
        new_condition,
        1,
    )


# Step 1c: upgrade an intermediate Evolution condition that already has
# LAST_RUN_AT but not the cutover-completion marker.
if "K2_EV_CUTOVER_DONE" not in func:
    last_run_clause = """      !String(
        PropertiesService
          .getScriptProperties()
          .getProperty('K2_EV_LAST_RUN_AT') || ''
      ).trim() ||
      shouldRunByProperty_("""

    upgraded_clause = """      !String(
        PropertiesService
          .getScriptProperties()
          .getProperty('K2_EV_LAST_RUN_AT') || ''
      ).trim() ||
      !String(
        PropertiesService
          .getScriptProperties()
          .getProperty('K2_EV_CUTOVER_DONE') || ''
      ).trim() ||
      shouldRunByProperty_("""

    if last_run_clause not in func:
        raise SystemExit(
            "PATCH_FAIL: cannot add K2 cutover completion gate"
        )

    func = func.replace(
        last_run_clause,
        upgraded_clause,
        1,
    )


# Step 2a: repair K2 readiness semantics.


# A saved K2 session (cookie + client id) is enough to continue syncing even
# when username/password were never persisted in Script Properties.
guard_pattern = re.compile(
    r"""\n\s*if \(!getMasterConfigurationState_\(\)\.k2Ready\) \{\s*
\s*throw new Error\(\s*
\s*'K2 не настроен: отсутствуют K2_USERNAME/K2_PASSWORD\. ' \+\s*
\s*'Запусти один раз saveK2Credentials\(\)\.'\s*
\s*\);\s*
\s*\}\s*""",
    re.S,
)
func = guard_pattern.sub("\n", func)

# Step 2b: make the daily K2/Ivanovo snapshot fail closed on source errors.
# When the history window opens, the master already forces both source reads.
# A failed source must not let stale sheet data become today's "fresh" history.
if "var historyK2Fresh = false;" not in func:
    history_decl = "  var historyDue = shouldTakeDailyStockSnapshot_();"
    if func.count(history_decl) != 1:
        raise SystemExit(
            "PATCH_FAIL: historyDue declaration not found exactly once"
        )

    func = func.replace(
        history_decl,
        history_decl
        + "\n  var historyK2Fresh = false;"
        + "\n  var historyIvanovoFresh = false;"
        + "\n  var k2FailedThisCycle = false;"
        + "\n  var ivanovoFailedThisCycle = false;",
        1,
    )

if "var k2FailedThisCycle = false;" not in func:
    history_decl = "  var historyIvanovoFresh = false;"
    if func.count(history_decl) != 1:
        raise SystemExit(
            "PATCH_FAIL: history safety declaration point not found"
        )

    func = func.replace(
        history_decl,
        history_decl
        + "\n  var k2FailedThisCycle = false;"
        + "\n  var ivanovoFailedThisCycle = false;",
        1,
    )


if "historyK2Fresh = true;" not in func:
    k2_success = "        k2Updated = Boolean(k2Result && k2Result.changed);"
    if func.count(k2_success) != 1:
        raise SystemExit(
            "PATCH_FAIL: K2 success point not found exactly once"
        )

    func = func.replace(
        k2_success,
        k2_success + "\n        historyK2Fresh = true;",
        1,
    )

if "historyIvanovoFresh = true;" not in func:
    iv_pattern = re.compile(
        r"(?m)^(\s*)exportFulfilmentStocks\(\);\s*$"
    )
    iv_matches = list(iv_pattern.finditer(func))

    if len(iv_matches) != 1:
        raise SystemExit(
            "PATCH_FAIL: Ivanovo success point not found exactly once"
        )

    match = iv_matches[0]
    indent = match.group(1)
    replacement = (
        indent
        + "exportFulfilmentStocks();\n"
        + indent
        + "historyIvanovoFresh = true;"
    )

    func = (
        func[:match.start()]
        + replacement
        + func[match.end():]
    )

safe_history_guard = (
    "if (historyDue && historyK2Fresh && historyIvanovoFresh) {"
)

if safe_history_guard not in func:
    history_comment = (
        "/* 5. Один дневной снимок остатков К2 + Иваново около 12:00 МСК. */"
    )
    pos = func.find(history_comment)
    if pos < 0:
        raise SystemExit(
            "PATCH_FAIL: daily history block marker not found"
        )

    guard_pos = func.find("if (historyDue) {", pos)
    if guard_pos < 0:
        raise SystemExit(
            "PATCH_FAIL: daily history guard not found"
        )

    func = (
        func[:guard_pos]
        + safe_history_guard
        + func[guard_pos + len("if (historyDue) {"):]
    )


# Needs/Telegram must never run from a source that failed in this cycle.
if "k2FailedThisCycle = true;" not in func:
    marker = "        cycleErrors.push('K2: ' + error.message);"
    if func.count(marker) != 1:
        raise SystemExit(
            "PATCH_FAIL: K2 catch marker not found exactly once"
        )
    func = func.replace(
        marker,
        marker + "\n        k2FailedThisCycle = true;",
        1,
    )

if "ivanovoFailedThisCycle = true;" not in func:
    marker = "        cycleErrors.push('Иваново: ' + error.message);"
    if func.count(marker) != 1:
        raise SystemExit(
            "PATCH_FAIL: Ivanovo catch marker not found exactly once"
        )
    func = func.replace(
        marker,
        marker + "\n        ivanovoFailedThisCycle = true;",
        1,
    )

if "wbOsAssertTrustedNeedsSources_(" not in func:
    marker = "      waitForStableNeedsSnapshot_();"
    if func.count(marker) != 1:
        raise SystemExit(
            "PATCH_FAIL: needs snapshot marker not found exactly once"
        )

    func = func.replace(
        marker,
        """      wbOsAssertTrustedNeedsSources_(
        k2FailedThisCycle,
        ivanovoFailedThisCycle
      );
      waitForStableNeedsSnapshot_();""",
        1,
    )


# Step 2c: independently migrate the post-refresh watchdog.

needle = "    refreshAutomationStatusSheet_();"
watchdog = """    refreshAutomationStatusSheet_();

    /*
     * K2 watchdog reads the now-refreshed heartbeat and sends Telegram only
     * when the effective state/fingerprint actually changes.
     */
    try {
      k2EvolutionWatchdogNotify_();
    } catch (watchdogError) {
      Logger.log(
        'K2 Evolution watchdog Telegram: ' +
        (watchdogError.stack || watchdogError.message)
      );
    }"""

if "k2EvolutionWatchdogNotify_();" not in func:
    if func.count(needle) != 1:
        raise SystemExit(
            "PATCH_FAIL: expected one refreshAutomationStatusSheet_ call "
            f"inside master, found={func.count(needle)}"
        )
    func = func.replace(needle, watchdog, 1)

# Step 2d: add WB public customer prices v4 to the master.
price_marker = "syncWbPublicCustomerPricesV4_(forceAll);"
if price_marker not in func:
    save_marker = "    saveMasterCycleResult_(cycleErrors);"
    if func.count(save_marker) != 1:
        raise SystemExit(
            "PATCH_FAIL: expected one saveMasterCycleResult_ call "
            f"inside master, found={func.count(save_marker)}"
        )

    price_block = """    /* WB OS · WB public customer prices v4 */
    try {
      syncWbPublicCustomerPricesV4_(forceAll);
    } catch (priceError) {
      cycleErrors.push('WB public prices: ' + priceError.message);

      try {
        wbPriceV4RecordFailure_(priceError);
      } catch (priceDiagError) {
        Logger.log(
          'WB public prices diagnostics: ' +
          priceDiagError.message
        );
      }

      Logger.log(
        'WB public prices v4: ' +
        (priceError.stack || priceError.message)
      );
    }

"""

    func = func.replace(
        save_marker,
        price_block + save_marker,
        1,
    )

# Step 2e: refresh the official seller-price source transactionally before
# public buyer-price calibration. The helper snapshots/restores "Цены" if the
# legacy DOM exporter fails after clearing that sheet.
if "wbSellerPriceRefreshIfDue_(forceAll);" not in func:
    price_comment = "    /* WB OS · WB public customer prices v4 */"

    if func.count(price_comment) != 1:
        raise SystemExit(
            "PATCH_FAIL: cannot place safe WB seller-price refresh"
        )

    seller_price_block = """    /* WB OS · official seller prices · transactional refresh */
    try {
      wbSellerPriceRefreshIfDue_(forceAll);
    } catch (sellerPriceError) {
      cycleErrors.push(
        'WB seller prices: ' + sellerPriceError.message
      );
      Logger.log(
        'WB seller prices: ' +
        (sellerPriceError.stack || sellerPriceError.message)
      );
    }

"""

    func = func.replace(
        price_comment,
        seller_price_block + price_comment,
        1,
    )


# Step 2i: bootstrap the dedicated Ozon radar minute trigger from the existing
# master cycle. This is idempotent and avoids any manual Apps Script run after
# hosted deployment.
if "ensureOzonRadarServerTrigger_();" not in func:
    save_marker = "    saveMasterCycleResult_(cycleErrors);"
    if func.count(save_marker) != 1:
        raise SystemExit(
            "PATCH_FAIL: cannot place Ozon radar trigger bootstrap"
        )

    ozon_trigger_block = """    /* Ozon LIVE radar · ensure dedicated 1-minute trigger */
    try {
      ensureOzonRadarServerTrigger_();
    } catch (ozonRadarTriggerError) {
      cycleErrors.push(
        'Ozon radar trigger: ' + ozonRadarTriggerError.message
      );
      Logger.log(
        'Ozon radar trigger: ' +
        (ozonRadarTriggerError.stack || ozonRadarTriggerError.message)
      );
    }

"""
    func = func.replace(
        save_marker,
        ozon_trigger_block + save_marker,
        1,
    )

# Rebuild after all master-function migrations.
new_text = before + func + after


# Step 2e: route the manual "WB Цены" menu action through the same
# transactional seller-price guard. The old entrypoint called the DOM exporter
# directly, and that exporter clears "Цены" before its API calls.
if "function domExportPrices()" in new_text and "SAFE_MANUAL_WB_PRICE_ENTRYPOINT" not in new_text:
    manual_price_pattern = re.compile(
        r"function\s+domExportPrices\s*\(\)\s*\{.*?\n\}",
        re.S,
    )

    manual_price_replacement = """function domExportPrices() {
  /* SAFE_MANUAL_WB_PRICE_ENTRYPOINT */
  wbSellerPriceRefreshIfDue_(true);
  return forceSyncWbPublicCustomerPricesV4();
}"""

    new_text, count = manual_price_pattern.subn(
        manual_price_replacement,
        new_text,
        count=1,
    )

    if count != 1:
        raise SystemExit(
            "PATCH_FAIL: cannot safely migrate domExportPrices"
        )


# Step 2f: add a public, side-effect-minimal bootstrap for deployment.

# Unlike setupFinalAutomation(), this only ensures the single master clock and
# does NOT force Ivanovo/Supplier/WB/Ozon/K2 jobs during deploy.
bootstrap_fn = """
function wbOsEnsureFinalAutomationTriggerAtomic_() {
  var lock = LockService.getDocumentLock();

  if (!lock) {
    ensureFinalAutomationTrigger_();
    return;
  }

  if (!lock.tryLock(10000)) {
    throw new Error(
      'MASTER_TRIGGER_LOCK_BUSY: не удалось атомарно проверить master-триггер.'
    );
  }

  try {
    ensureFinalAutomationTrigger_();
  } finally {
    lock.releaseLock();
  }
}

function wbOsEnsureMasterTrigger() {
  wbOsEnsureFinalAutomationTriggerAtomic_();
  return 'MASTER_TRIGGER_OK';
}
"""

if "function wbOsEnsureMasterTrigger()" not in new_text:
    insertion = "\nfunction shouldRunByProperty_"
    if insertion not in new_text:
        raise SystemExit(
            "PATCH_FAIL: cannot place wbOsEnsureMasterTrigger bootstrap"
        )

    new_text = new_text.replace(
        insertion,
        "\n" + bootstrap_fn + insertion,
        1,
    )


# Step 2f: make the property-based master guard atomic without taking the
# ScriptLock that Ivanovo/Supplier/K2/price writers legitimately use inside the
# master cycle.
if "MASTER_RUN_GUARD_DOCUMENT_LOCK" not in new_text:
    guard_pattern = re.compile(
        r"function\s+acquireMasterRunGuard_\s*\(\)\s*\{.*?\n\}\n\nfunction\s+releaseMasterRunGuard_",
        re.S,
    )

    guard_replacement = """function acquireMasterRunGuard_() {
  /* MASTER_RUN_GUARD_DOCUMENT_LOCK */
  var lock = LockService.getDocumentLock();

  if (lock && !lock.tryLock(5000)) {
    return false;
  }

  try {
    var props = PropertiesService.getScriptProperties();
    var key = 'MASTER_AUTOMATION_RUNNING_AT';
    var existing = props.getProperty(key);

    if (existing) {
      var startedAt = new Date(existing);

      if (!isNaN(startedAt.getTime())) {
        var ageMinutes =
          (Date.now() - startedAt.getTime()) /
          60000;

        if (
          ageMinutes <
          MASTER_AUTOMATION_CFG.BUSY_TTL_MINUTES
        ) {
          return false;
        }
      }
    }

    props.setProperty(
      key,
      new Date().toISOString()
    );

    return true;
  } finally {
    if (lock) {
      lock.releaseLock();
    }
  }
}

function releaseMasterRunGuard_"""

    new_text, count = guard_pattern.subn(
        guard_replacement,
        new_text,
        count=1,
    )

    if count != 1:
        raise SystemExit(
            "PATCH_FAIL: cannot atomically migrate master run guard"
        )


# Step 2g: add the fail-closed source-freshness gate used before

# needs calculation / Telegram notifications.
trusted_needs_fn = """
function wbOsAssertTrustedNeedsSources_(
  k2FailedThisCycle,
  ivanovoFailedThisCycle
) {
  if (k2FailedThisCycle) {
    throw new Error(
      'NEEDS_BLOCKED_K2_ERROR: K2 упал в текущем master-цикле. ' +
      'Потребность/Telegram по старым остаткам запрещены.'
    );
  }

  if (ivanovoFailedThisCycle) {
    throw new Error(
      'NEEDS_BLOCKED_IVANOVO_ERROR: Иваново упало в текущем master-цикле. ' +
      'Потребность/Telegram по старым остаткам запрещены.'
    );
  }

  var props = PropertiesService.getScriptProperties();
  var checks = [
    {
      name: 'K2',
      key: 'K2_LAST_SUCCESS_AT',
      maxAgeMinutes: 30
    },
    {
      name: 'Иваново',
      key: 'IVANOVO_LAST_SUCCESS_AT',
      maxAgeMinutes: 240
    }
  ];

  for (var i = 0; i < checks.length; i++) {
    var cfg = checks[i];
    var raw = String(
      props.getProperty(cfg.key) || ''
    ).trim();

    if (!raw) {
      throw new Error(
        'NEEDS_BLOCKED_NO_SUCCESS: нет успешного heartbeat источника ' +
        cfg.name + '.'
      );
    }

    var date = new Date(raw);

    if (isNaN(date.getTime())) {
      throw new Error(
        'NEEDS_BLOCKED_BAD_HEARTBEAT: некорректный heartbeat источника ' +
        cfg.name + '.'
      );
    }

    var ageMinutes =
      (Date.now() - date.getTime()) /
      60000;

    if (
      ageMinutes < -5 ||
      ageMinutes > cfg.maxAgeMinutes
    ) {
      throw new Error(
        'NEEDS_BLOCKED_STALE_SOURCE: ' +
        cfg.name +
        ' age=' +
        Math.round(ageMinutes) +
        ' min; max=' +
        cfg.maxAgeMinutes +
        '.'
      );
    }
  }
}
"""

if "function wbOsAssertTrustedNeedsSources_(" not in new_text:
    insertion = "\nfunction shouldRunByProperty_"
    if insertion not in new_text:
        raise SystemExit(
            "PATCH_FAIL: cannot place trusted-needs source gate"
        )

    new_text = new_text.replace(
        insertion,
        "\n" + trusted_needs_fn + insertion,
        1,
    )


# Step 2h: make K2 readiness session-aware after final rebuild.


# This affects status/UI only. The master no longer blocks K2 on k2Ready;
# getK2WarehouseItems_ itself owns session reuse / relogin behavior.
old_ready = """k2Ready: Boolean(
      String(props.getProperty('K2_USERNAME') || '').trim() &&
      String(props.getProperty('K2_PASSWORD') || '')
    ),"""
new_ready = """k2Ready: Boolean(
      (
        String(props.getProperty('K2_USERNAME') || '').trim() &&
        String(props.getProperty('K2_PASSWORD') || '')
      ) || (
        String(props.getProperty('K2_SESSION_COOKIE') || '').trim() &&
        String(props.getProperty('K2_CLIENT_ID') || '').trim()
      )
    ),"""
if old_ready in new_text:
    new_text = new_text.replace(old_ready, new_ready, 1)
# If the function already differs from this exact historical form, do not fail
# the deploy. The hard safety property is that the master does not gate K2 on
# getMasterConfigurationState_().k2Ready anymore.

# Step 4: independently migrate history heartbeat self-healing.
old_history = "buildAutomationStatusRow_('История остатков', props.getProperty('FF_STOCK_HISTORY_LAST_AT'), 1560)"
new_history = "buildAutomationStatusRow_('История остатков', k2EvolutionHistoryLastAt_(props), 1560)"
if old_history in new_text:
    new_text = new_text.replace(old_history, new_history, 1)
elif new_history not in new_text:
    raise SystemExit("PATCH_FAIL: history status source not found")

# Manual price menu entrypoint must never bypass the transactional guard.
if "function domExportPrices()" in new_text:
    manual_start = new_text.find("function domExportPrices()")
    manual_end = new_text.find("\n}", manual_start)
    manual_block = new_text[manual_start:manual_end + 2]

    if "SAFE_MANUAL_WB_PRICE_ENTRYPOINT" not in manual_block:
        raise SystemExit(
            "PATCH_FAIL: domExportPrices remains unsafe"
        )


# Hard post-conditions. A successful patch is fully migrated, not half-done.
required = [
    "k2EvolutionFetchAndApply_()",
    "k2EvolutionRecordFailure_(error)",
    "k2EvolutionWatchdogNotify_();",
    "k2EvolutionHistoryLastAt_(props)",
    "syncWbPublicCustomerPricesV4_(forceAll);",
    "wbPriceV4RecordFailure_(priceError)",
    "function wbOsEnsureMasterTrigger()",
    "function wbOsEnsureFinalAutomationTriggerAtomic_()",
    "MASTER_RUN_GUARD_DOCUMENT_LOCK",
    "K2_EV_LAST_RUN_AT",
    "K2_EV_CUTOVER_DONE",
    "historyK2Fresh = true;",
    "historyIvanovoFresh = true;",
    "if (historyDue && historyK2Fresh && historyIvanovoFresh) {",
    "k2FailedThisCycle = true;",
    "ivanovoFailedThisCycle = true;",
    "function wbOsAssertTrustedNeedsSources_",
    "NEEDS_BLOCKED_STALE_SOURCE",
    "ensureOzonRadarServerTrigger_();",
]
missing = [marker for marker in required if marker not in new_text]
if missing:
    raise SystemExit("PATCH_FAIL: post-condition missing: " + ", ".join(missing))

# Hard safety rule: the master must never block a valid saved K2 session
# just because username/password are not persisted.
master_start = new_text.find("function runFinalAutomationCycle_")
master_end = new_text.find("\nfunction shouldRunByProperty_", master_start)
master_text = (
    new_text[master_start:master_end]
    if master_start >= 0 and master_end > master_start
    else ""
)
if "getMasterConfigurationState_().k2Ready" in master_text:
    raise SystemExit(
        "PATCH_FAIL: legacy K2 readiness guard still present in master"
    )


# The status/UI readiness function itself must explicitly accept a saved K2
# session. Checking for K2_SESSION_COOKIE somewhere in the master file is not
# enough: another helper could contain the same string and give a false pass.
cfg_match = re.search(
    r"function\s+getMasterConfigurationState_\s*\(\)\s*\{(.*?)\n\}",
    new_text,
    re.S,
)
if not cfg_match:
    raise SystemExit(
        "PATCH_FAIL: getMasterConfigurationState_ function not found"
    )

cfg_text = cfg_match.group(1)
for marker in (
    "K2_USERNAME",
    "K2_PASSWORD",
    "K2_SESSION_COOKIE",
    "K2_CLIENT_ID",
):
    if marker not in cfg_text:
        raise SystemExit(
            "PATCH_FAIL: session-aware readiness missing inside "
            "getMasterConfigurationState_: " + marker
        )

path.write_text(new_text, encoding="utf-8")

print("PATCH_OK:", path.relative_to(root))
