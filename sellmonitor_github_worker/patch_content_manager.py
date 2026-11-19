#!/usr/bin/env python3
"""Install the protected WB content manager action into the existing Apps Script adapter."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_content_manager.py <apps-script-source-root>")

root = Path(sys.argv[1]).resolve()
worker = root / "SELLMONITOR_GITHUB_WORKER.gs"
helper_source = Path(__file__).with_name("WB_CONTENT_MANAGER_SYNC.gs")
helper_targets = [root / "WB_CONTENT_MANAGER_SYNC.gs", root / "WB_CONTENT_MANAGER_SYNC.js"]
existing_helper_targets = [path for path in helper_targets if path.is_file()]
if len(existing_helper_targets) > 1:
    raise SystemExit("WB content manager helper exists with conflicting extensions")
helper_target = existing_helper_targets[0] if existing_helper_targets else helper_targets[0]
if not worker.is_file() or not helper_source.is_file():
    raise SystemExit("WB content manager adapter source is missing")

source = worker.read_text(encoding="utf-8")
marker = "WB_CONTENT_MANAGER_SYNC_ROUTE_V1"
anchor = """    if (action === 'publish_search_positions') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishRuntimeSearch_(body.cabinet || '', body.snapshot || {}));
    }"""
route = """    /* WB_CONTENT_MANAGER_SYNC_ROUTE_V1 */
    if (action === 'wb_content_manager_sync') {
      var contentManagerResult = sellmonitorGithubPublishContentManagerSnapshot_(body);
      return sellmonitorGithubWebJson_({
        ok: contentManagerResult && contentManagerResult.ok === true,
        action: action,
        result: contentManagerResult
      });
    }"""
if marker not in source:
    if source.count(anchor) != 1:
        raise SystemExit("WB content manager route insertion anchor changed")
    source = source.replace(anchor, anchor + "\n\n" + route, 1)
    worker.write_text(source, encoding="utf-8")

helper_target.write_text(helper_source.read_text(encoding="utf-8"), encoding="utf-8")
print("WB_CONTENT_MANAGER_ROUTE=installed")
print("WB_CONTENT_MANAGER_HELPER=installed")
