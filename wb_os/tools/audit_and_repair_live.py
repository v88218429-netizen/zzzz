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
    for p in k2:
        if p == keeper:
            continue
        p.write_text(
            "// WB OS: legacy duplicate K2 core disabled by audited deploy.\n"
            "// Canonical K2 core: " + keeper.name + "\n",
            encoding="utf-8",
        )
        print("DISABLED_DUPLICATE_K2:", p.relative_to(root))
else:
    keeper = k2[0]

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

# 3) No duplicate global function definitions are allowed after repair.
defs = {}
duplicates = {}
pattern = re.compile(r"(?m)^\s*function\s+([A-Za-z_$][\w$]*)\s*\(")

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

# 4) Critical modules must exist exactly once.
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

print("AUDIT_OK: global function namespace is collision-free")
