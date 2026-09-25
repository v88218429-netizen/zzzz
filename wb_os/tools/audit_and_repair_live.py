#!/usr/bin/env python3
import pathlib
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: audit_and_repair_live.py <apps-script-root>")

root = pathlib.Path(sys.argv[1]).resolve()
files = []
for ext in ("*.gs", "*.js"):
    files.extend(root.rglob(ext))

def read(path):
    return path.read_text(encoding="utf-8", errors="ignore")

def sources():
    return [p for p in files if p.exists()]

def find(marker):
    return [p for p in sources() if marker in read(p)]

# 1) K2 core: Apps Script has a single global namespace.
# Keep the newer stable implementation and neutralize an older full duplicate.
k2 = find("function getK2WarehouseItems_")
if not k2:
    raise SystemExit("AUDIT_FAIL: K2 core not found")

if len(k2) > 1:
    preferred = [
        p for p in k2
        if "function getK2CredentialsWithFallback_" in read(p)
        and "function updateK2AutomationStatus_" in read(p)
    ]
    if len(preferred) != 1:
        names = [str(p.relative_to(root)) for p in k2]
        raise SystemExit(
            "AUDIT_FAIL: multiple K2 cores and canonical core is ambiguous: "
            + repr(names)
        )

    keeper = preferred[0]

    function_pattern = re.compile(
        r"(?m)^function\s+([A-Za-z_$][\w$]*)\s*\("
    )
    global_decl_pattern = re.compile(
        r"(?m)^(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*(?:=|;)"
    )

    keeper_text = read(keeper)
    keeper_functions = set(function_pattern.findall(keeper_text))
    keeper_globals = set(global_decl_pattern.findall(keeper_text))

    for p in k2:
        if p == keeper:
            continue

        legacy_text = read(p)
        legacy_functions = set(function_pattern.findall(legacy_text))
        legacy_globals = set(global_decl_pattern.findall(legacy_text))

        unique_legacy = sorted(legacy_functions - keeper_functions)
        unique_legacy_globals = sorted(legacy_globals - keeper_globals)

        # Never erase a K2 file that contains behavior/state not present in
        # the selected canonical implementation. In that case fail closed and
        # require a manual merge instead of guessing.
        if unique_legacy or unique_legacy_globals:
            raise SystemExit(
                "AUDIT_FAIL: duplicate K2 core has unique declarations and "
                "cannot be disabled safely: "
                + str(p.relative_to(root))
                + " unique_functions="
                + repr(unique_legacy)
                + " unique_globals="
                + repr(unique_legacy_globals)
            )

        p.write_text(
            "// WB OS: legacy duplicate K2 core disabled by audited deploy.\n"
            "// Canonical K2 core: " + keeper.name + "\n",
            encoding="utf-8",
        )
        print("DISABLED_DUPLICATE_K2:", p.relative_to(root))
else:
    keeper = k2[0]


def repair_k2_credentials(text):
    """
    Make K2 credentials resilient across older live project variants.

    Older revisions could persist credentials in UserProperties while newer
    loginK2_ implementations read ScriptProperties only. During audited deploy
    we install a fallback reader that searches Script/User/Document properties
    and migrates any valid pair back into ScriptProperties.
    """
    helper_marker = "function getK2CredentialsWithFallback_()"

    if helper_marker not in text:
        login_pos = text.find("function loginK2_()")
        if login_pos < 0:
            raise SystemExit(
                "AUDIT_FAIL: K2 core has no loginK2_ credential entrypoint"
            )

        helper = r"""function getK2CredentialsWithFallback_() {
  var stores = [
    {
      name: 'ScriptProperties',
      store: PropertiesService.getScriptProperties()
    },
    {
      name: 'UserProperties',
      store: PropertiesService.getUserProperties()
    }
  ];

  try {
    stores.push({
      name: 'DocumentProperties',
      store: PropertiesService.getDocumentProperties()
    });
  } catch (ignore) {}

  for (var i = 0; i < stores.length; i++) {
    var store = stores[i].store;
    if (!store) continue;

    var username = String(
      store.getProperty('K2_USERNAME') ||
      store.getProperty('K2_LOGIN') ||
      store.getProperty('K2_USER') ||
      ''
    ).trim();

    var password = String(
      store.getProperty('K2_PASSWORD') ||
      store.getProperty('K2_PASS') ||
      ''
    );

    if (username && password) {
      PropertiesService
        .getScriptProperties()
        .setProperties({
          K2_USERNAME: username,
          K2_PASSWORD: password
        });

      return {
        username: username,
        password: password,
        source: stores[i].name
      };
    }
  }

  return {
    username: '',
    password: '',
    source: ''
  };
}


"""

        text = text[:login_pos] + helper + text[login_pos:]
        print("K2_CREDENTIAL_FALLBACK_ADDED")

    login_start = text.find("function loginK2_()")
    if login_start < 0:
        raise SystemExit("AUDIT_FAIL: K2 loginK2_ not found")

    login_end = text.find("\nfunction ", login_start + 1)
    if login_end < 0:
        login_end = len(text)

    login_text = text[login_start:login_end]

    if "getK2CredentialsWithFallback_();" not in login_text:
        username_pos = login_text.find("var username")
        guard_pos = login_text.find("if (!username)")

        if username_pos < 0 or guard_pos < 0 or guard_pos <= username_pos:
            raise SystemExit(
                "AUDIT_FAIL: K2 login credential layout is unknown; "
                "refusing to guess"
            )

        legacy_chunk = login_text[username_pos:guard_pos]

        if (
            "K2_USERNAME" not in legacy_chunk
            or "K2_PASSWORD" not in legacy_chunk
            or "var password" not in legacy_chunk
        ):
            raise SystemExit(
                "AUDIT_FAIL: K2 login credential block is not the expected "
                "username/password reader"
            )

        replacement = """var credentials =
    getK2CredentialsWithFallback_();

  var username = credentials.username;
  var password = credentials.password;

  """

        login_text2 = (
            login_text[:username_pos]
            + replacement
            + login_text[guard_pos:]
        )

        text = (
            text[:login_start]
            + login_text2
            + text[login_end:]
        )
        print("K2_CREDENTIAL_FALLBACK_WIRED")

    return text


def repair_k2_refresh_metadata(text):
    """
    Keep two different K2 timestamps in the live stock sheet:
    - source change time from K2;
    - time when Google Sheets actually received the snapshot.

    Also make the manual sync path record a successful K2 run, so watchdog
    status reflects a real successful fetch/write instead of remaining red.
    """
    write_marker = "function writeK2StocksToSheet_(items)"
    if write_marker in text:
        write_start = text.find(write_marker)
        write_end = text.find("\nfunction ", write_start + 1)
        if write_end < 0:
            write_end = len(text)

        write_text = text[write_start:write_end]

        if "'Обновлено в таблице'" not in write_text:
            old_header = """    'Мин. остаток',
    'Дата обновления К2'
  ];"""
            new_header = """    'Мин. остаток',
    'Изменено в K2',
    'Обновлено в таблице'
  ];"""
            if old_header not in write_text:
                raise SystemExit(
                    "AUDIT_FAIL: K2 stock header layout is unknown; "
                    "cannot add table refresh timestamp safely"
                )
            write_text = write_text.replace(old_header, new_header, 1)

            if "var tableUpdatedAt = new Date();" not in write_text:
                if "  var output = [];" not in write_text:
                    raise SystemExit(
                        "AUDIT_FAIL: K2 output buffer layout is unknown; "
                        "cannot stamp table refresh time safely"
                    )
                write_text = write_text.replace(
                    "  var output = [];",
                    "  var tableUpdatedAt = new Date();\n  var output = [];",
                    1,
                )

            old_value = """      String(
        item.updatedAt || ''
      )
    ]);"""
            new_value = """      String(
        item.updatedAt || ''
      ),
      tableUpdatedAt
    ]);"""
            if old_value not in write_text:
                raise SystemExit(
                    "AUDIT_FAIL: K2 output row layout is unknown; "
                    "cannot append table refresh timestamp safely"
                )
            write_text = write_text.replace(old_value, new_value, 1)

            if "setNumberFormat('yyyy-mm-dd hh:mm:ss')" not in write_text:
                numeric_format = """  sheet
    .getRange(
      2,
      3,
      output.length,
      3
    )
    .setNumberFormat('0');

  sheet.setFrozenRows(1);"""
                date_format = """  sheet
    .getRange(
      2,
      3,
      output.length,
      3
    )
    .setNumberFormat('0');

  sheet
    .getRange(
      2,
      6,
      output.length,
      2
    )
    .setNumberFormat('yyyy-mm-dd hh:mm:ss');

  sheet.setFrozenRows(1);"""
                if numeric_format not in write_text:
                    raise SystemExit(
                        "AUDIT_FAIL: K2 number-format layout is unknown; "
                        "cannot format dual timestamps safely"
                    )
                write_text = write_text.replace(
                    numeric_format,
                    date_format,
                    1,
                )

            text = text[:write_start] + write_text + text[write_end:]
            print("K2_DUAL_TIMESTAMPS_ADDED")

    sync_marker = "function syncK2StocksOnly()"
    sync_start = text.find(sync_marker)
    if sync_start >= 0:
        sync_end = text.find("\nfunction ", sync_start + 1)
        if sync_end < 0:
            sync_end = len(text)

        sync_text = text[sync_start:sync_end]

        if (
            "writeK2StocksToSheet_(items);" in sync_text
            and "SpreadsheetApp.flush();" in sync_text
            and "saveK2SyncSuccess_(items.length);" not in sync_text
        ):
            sync_text = sync_text.replace(
                "  SpreadsheetApp.flush();",
                "  SpreadsheetApp.flush();\n\n"
                "  saveK2SyncSuccess_(items.length);",
                1,
            )
            text = text[:sync_start] + sync_text + text[sync_end:]
            print("K2_MANUAL_SUCCESS_STATUS_ADDED")

    return text


def inject_master_trigger_bootstrap(text, function_name, marker):
    if marker in text:
        return text, "already"

    pattern = re.compile(
        r"(function\s+"
        + re.escape(function_name)
        + r"\s*\([^)]*\)\s*\{\s*)"
    )

    block = (
        r"\1"
        + "\n  /* " + marker + " */\n"
        + "  try {\n"
        + "    if (typeof wbOsEnsureFinalAutomationTriggerAtomic_ === 'function') {\n"
        + "      wbOsEnsureFinalAutomationTriggerAtomic_();\n"
        + "    } else if (typeof ensureFinalAutomationTrigger_ === 'function') {\n"
        + "      ensureFinalAutomationTrigger_();\n"
        + "    }\n"
        + "  } catch (masterTriggerError) {\n"
        + "    Logger.log(\n"
        + "      'WB OS master-trigger bootstrap skipped: ' +\n"
        + "      String(\n"
        + "        masterTriggerError && masterTriggerError.message\n"
        + "          ? masterTriggerError.message\n"
        + "          : masterTriggerError\n"
        + "      )\n"
        + "    );\n"
        + "  }\n"
    )

    out, count = pattern.subn(block, text, count=1)
    if count != 1:
        return text, "missing"

    return out, "changed"


# The live K2 installer currently schedules syncK2StocksOnly every 10 min,
# and that legacy function did not use ScriptLock. Wrap it instead of rewriting
# its proven API/write logic, so the legacy writer and K2 Evolution cannot race.
k2_text = read(keeper)
k2_text = repair_k2_credentials(k2_text)
k2_text = repair_k2_refresh_metadata(k2_text)

if "function syncK2StocksOnlyLegacy_()" not in k2_text:
    sync_only_marker = "function syncK2StocksOnly() {"

    if k2_text.count(sync_only_marker) != 1:
        raise SystemExit(
            "AUDIT_FAIL: syncK2StocksOnly definition not found exactly once"
        )

    wrapper = """function syncK2StocksOnly() {
  if (typeof k2EvolutionFetchAndApply_ === 'function') {
    return k2EvolutionFetchAndApply_();
  }

  var lock = LockService.getScriptLock();

  if (!lock.tryLock(30000)) {
    Logger.log(
      'K2 legacy sync пропущен: другой K2/FF writer уже выполняется.'
    );
    return;
  }

  try {
    return syncK2StocksOnlyLegacy_();
  } finally {
    lock.releaseLock();
  }
}


function syncK2StocksOnlyLegacy_() {"""

    k2_text = k2_text.replace(
        sync_only_marker,
        wrapper,
        1,
    )

    print("LOCKED_LEGACY_K2_SYNC_ONLY:", keeper.relative_to(root))


# Normalize legacy K2 lock variable names. The historical source has two
# function-local "var lock" declarations starting at column 0; the namespace
# audit intentionally treats column-0 declarations as suspicious, so make
# these names explicit and collision-free.
def namespace_function_lock(text, function_name, new_name):
    marker = "function " + function_name + "()"
    start = text.find(marker)
    if start < 0:
        return text
    end = text.find("\nfunction ", start + len(marker))
    if end < 0:
        end = len(text)
    block = text[start:end]
    if "var lock =" not in block:
        return text
    block = block.replace("var lock =", "var " + new_name + " =", 1)
    block = block.replace("lock.tryLock(", new_name + ".tryLock(", 1)
    block = block.replace("lock.releaseLock()", new_name + ".releaseLock()", 1)
    return text[:start] + block + text[end:]

k2_text_before_locks = k2_text
k2_text = namespace_function_lock(
    k2_text, "syncK2StocksAndNotify", "k2NotifyLock"
)
k2_text = namespace_function_lock(
    k2_text, "syncK2StocksOnly", "k2OnlyLock"
)
if k2_text != k2_text_before_locks:
    print("K2_LOCK_NAMESPACED")

# Existing K2 timers are a safe bootstrap path if the master trigger is absent.
# They keep their legacy K2 behavior, but first ensure the single master clock.
for fn in ("syncK2StocksOnly", "syncK2StocksAndNotify"):
    marker = "WB_OS_MASTER_TRIGGER_BOOTSTRAP_" + fn
    k2_text, status = inject_master_trigger_bootstrap(
        k2_text,
        fn,
        marker,
    )

    if status == "missing":
        raise SystemExit(
            "AUDIT_FAIL: cannot patch master-trigger bootstrap into K2 function: "
            + fn
        )

    if status == "changed":
        print("MASTER_BOOTSTRAP_K2:", fn)

keeper.write_text(k2_text, encoding="utf-8")

# The SPP monitor is known to be actively running in this project. Preserve
# its price/history/Telegram behavior, but let its scheduled tick also restore
# the master trigger if that trigger is ever missing.
spp_modules = [
    p for p in sources()
    if "function sppMonitorScheduledTick()" in read(p)
    and "function sppRunMonitor_" in read(p)
]
if len(spp_modules) > 1:
    raise SystemExit(
        "AUDIT_FAIL: multiple SPP monitor modules: "
        + repr([str(p.relative_to(root)) for p in spp_modules])
    )

if spp_modules:
    p = spp_modules[0]
    text = read(p)
    text, status = inject_master_trigger_bootstrap(
        text,
        "sppMonitorScheduledTick",
        "WB_OS_MASTER_TRIGGER_BOOTSTRAP_sppMonitorScheduledTick",
    )

    if status == "missing":
        raise SystemExit(
            "AUDIT_FAIL: cannot patch master-trigger bootstrap into SPP scheduled tick"
        )

    if status == "changed":
        p.write_text(text, encoding="utf-8")
        print("MASTER_BOOTSTRAP_SPP:", p.relative_to(root))

print("K2_CORE:", keeper.relative_to(root))

# 2) Supplier Orders had helper names colliding with MASTER helper names.
# Namespace only the private Supplier helpers and every use inside that file.
supplier = [
    p for p in sources()
    if "var SUPPLIER_ORDERS_CFG" in read(p)
    and "function syncAllSupplierOrdersNow" in read(p)
]
if len(supplier) > 1:
    raise SystemExit(
        "AUDIT_FAIL: multiple canonical Supplier Orders modules: "
        + repr([str(p.relative_to(root)) for p in supplier])
    )

uo_supplier = [
    p for p in sources()
    if "var UO_CFG" in read(p)
    and "function UO_syncAllSupplierOrdersNow" in read(p)
]

if supplier and uo_supplier:
    print(
        "AUDIT_WARN_PARALLEL_SUPPLIER_MODULES: "
        "canonical Supplier + UO Supplier both exist; "
        "their behavior is not identical, so deploy leaves both modules "
        "and their triggers untouched."
    )

if supplier:
    p = supplier[0]
    text = read(p)
    renames = {
        "findSummaryHeaderRow_": "supplierFindSummaryHeaderRow_",
        "hasHeaderAlias_": "supplierHasHeaderAlias_",
        "requireHeaderColumn_": "supplierRequireHeaderColumn_",
        "optionalHeaderColumn_": "supplierOptionalHeaderColumn_",
        "columnIndexToLetter_": "supplierColumnIndexToLetter_",
        "parseNumber_": "supplierParseNumber_",
    }
    changed = False
    for old, new in renames.items():
        if re.search(r"\b" + re.escape(old) + r"\b", text):
            text = re.sub(r"\b" + re.escape(old) + r"\b", new, text)
            changed = True

    if changed:
        p.write_text(text, encoding="utf-8")
        print("NAMESPACED_SUPPLIER_HELPERS:", p.relative_to(root))

# 3) Repair the old manual WB price entrypoint if it exists.
# The legacy implementation uses getActiveSheet() and can write column K on
# whichever tab happens to be open. Preserve the public function name, but
# route it to the audited v4 engine.
legacy_price = find("function updateWbPricesFromLinks()")
if len(legacy_price) > 1:
    raise SystemExit(
        "AUDIT_FAIL: multiple updateWbPricesFromLinks entrypoints: "
        + repr([str(p.relative_to(root)) for p in legacy_price])
    )

if legacy_price:
    p = legacy_price[0]
    text = read(p)
    pattern = re.compile(
        r"function\s+updateWbPricesFromLinks\s*\(\)\s*\{.*?\n\}\s*\n\s*function\s+columnToIndex_",
        re.S,
    )
    replacement = """function updateWbPricesFromLinks() {
  return forceSyncWbPublicCustomerPricesV4();
}

function columnToIndex_"""
    text2, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(
            "AUDIT_FAIL: legacy updateWbPricesFromLinks structure is unknown; "
            "refusing to patch it by guess"
        )
    p.write_text(text2, encoding="utf-8")
    print("COMPAT_PRICE_ENTRYPOINT:", p.relative_to(root))

# 4) No duplicate global function definitions are allowed after repair.
defs = {}
duplicates = {}
pattern = re.compile(r"(?m)^function\s+([A-Za-z_$][\w$]*)\s*\(")

for p in sources():
    text = read(p)
    for match in pattern.finditer(text):
        name = match.group(1)
        if name in defs:
            duplicates.setdefault(name, [defs[name]]).append(p)
        else:
            defs[name] = p

if duplicates:
    parts = []
    for name in sorted(duplicates):
        names = [str(p.relative_to(root)) for p in duplicates[name]]
        parts.append(name + "=" + ",".join(names))
    raise SystemExit(
        "AUDIT_FAIL: duplicate global functions remain: " + "; ".join(parts)
    )

# 5) Top-level global variables/config objects must not be declared in
# multiple files. Apps Script joins all source files into one global namespace,
# so duplicate config variables can silently overwrite each other.
global_defs = {}
global_dups = {}
global_pattern = re.compile(
    r"(?m)^(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*(?:=|;)"
)

for p in sources():
    text = read(p)
    for match in global_pattern.finditer(text):
        name = match.group(1)
        if name in global_defs:
            global_dups.setdefault(name, [global_defs[name]]).append(p)
        else:
            global_defs[name] = p

if global_dups:
    parts = []
    for name in sorted(global_dups):
        names = [str(p.relative_to(root)) for p in global_dups[name]]
        parts.append(name + "=" + ",".join(names))
    raise SystemExit(
        "AUDIT_FAIL: duplicate top-level globals remain: "
        + "; ".join(parts)
    )

# 6) Critical modules must exist exactly once.
critical = [
    "function runFinalAutomationCycle_",
    "function getK2WarehouseItems_",
    "function k2EvolutionFetchAndApply_",
    "function syncWbPublicCustomerPricesV4_",
]
for marker in critical:
    hits = find(marker)
    if len(hits) != 1:
        raise SystemExit(
            "AUDIT_FAIL: critical marker count "
            + marker + " = " + str(len(hits))
        )

print("AUDIT_OK: global function/config namespace is collision-free")
