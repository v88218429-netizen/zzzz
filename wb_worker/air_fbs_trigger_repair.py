#!/usr/bin/env python3
"""Patch AIR's bound Apps Script so scheduled FBS reads run through GitHub only."""
from __future__ import annotations
import hashlib
from pathlib import Path
import re
import sys

root=Path(sys.argv[1])
code_path=root/"Код.js"
ingress_path=root/"GithubTokenIngress.js"
code=code_path.read_text(encoding="utf-8")
ingress=ingress_path.read_text(encoding="utf-8")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count=text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        raise SystemExit(f"AIR_PATCH_EXPECTED_ONE_{label}_FOUND_{count}")
    return text.replace(old,new,1)

def replace_function(text: str, name: str, replacement: str) -> str:
    start_marker="function "+name+"("
    start=text.find(start_marker)
    if start < 0:
        raise SystemExit("AIR_PATCH_FUNCTION_NOT_FOUND:"+name)
    next_function=re.search(r"\nfunction [A-Za-z0-9_$]+\s*\(",text[start+len(start_marker):])
    end=(start+len(start_marker)+next_function.start()+1) if next_function else len(text)
    current=text[start:end].rstrip()
    if current.startswith(replacement.strip()):
        return text
    return text[:start]+replacement.rstrip()+"\n\n"+text[end:].lstrip("\n")

# Disable the obsolete five-minute local WB poller and keep its handler harmless
# in case a previously queued Apps Script event still starts after deletion.
code=replace_function(code,"scheduledFastCheck",'''function scheduledFastCheck() {
  return wbFbsCleanupLegacyFbsTriggersFromTimerV1_();
}''')
code=replace_once(
    code,
    "  ScriptApp.newTrigger('scheduledFastCheck').timeBased().everyMinutes(5).create();\n",
    "",
    "REMOVE_MINIMAL_FASTCHECK_TRIGGER"
)
code=replace_once(
    code,
    "  const handlers = ['scheduledFastCheck', 'scheduledDailyMaintenance'];",
    "  const handlers = ['scheduledDailyMaintenance'];",
    "REMOVE_FASTCHECK_FROM_TRIGGER_REPAIR"
)
code=replace_once(
    code,
    "  // Если старая full-sync/fast-check очередь сохранилась в properties,\n"
    "  // возвращаем только её continuation, не перезапуская данные с нуля.\n"
    "  try {\n"
    "    if (isFullSyncPendingV292_()) scheduleFullSyncContinuationV292_();\n"
    "  } catch (error) { console.log(error); }\n"
    "  try {\n"
    "    if (isFastCheckPendingV293_()) scheduleFastCheckContinuationV293_();\n"
    "    else if (toBoolean_(getSetting_('MONITORING_ENABLED', APP.DEFAULTS.MONITORING_ENABLED))) {\n"
    "      requestFastCheckV293_(false);\n"
    "    }\n"
    "  } catch (error) { console.log(error); }\n",
    "  // Legacy UrlFetch queues are retired; GitHub Actions is the FBS scheduler.\n"
    "  clearFastCheckStateV293_(true);\n"
    "  clearFullSyncStateV292_(true);\n",
    "DISABLE_AUTOMATION_REQUEUE"
)
code=replace_function(code,"scheduledFastCheckContinuationV293_",'''function scheduledFastCheckContinuationV293_() {
  return wbFbsCleanupLegacyFbsTriggersFromTimerV1_();
}''')
code=replace_function(code,"scheduledFullSyncContinuationV292_",'''function scheduledFullSyncContinuationV292_() {
  return wbFbsCleanupLegacyFbsTriggersFromTimerV1_();
}''')
code=replace_function(code,"requestFastCheckV293_",'''function requestFastCheckV293_(manualStart) {
  if (manualStart) {
    try {
      getSpreadsheet_().toast(
        'Обновление FBS выполняется через GitHub Actions.',
        'WB FBS',
        8
      );
    } catch (error) {
      console.log(error);
    }
  }
  return false;
}''')
code=replace_once(
    code,
    "function scheduledFastCheck() {\n  // AIR FBS polling is owned by GitHub Actions; this legacy timer is retired.\n  return;\n}",
    "/* AIR_FBS_GITHUB_ONLY_LEGACY_FASTCHECK_DISABLED_V1 */\n"
    "function scheduledFastCheck() {\n"
    "  // AIR FBS polling is owned by GitHub Actions; this legacy timer is retired.\n"
    "  return;\n"
    "}",
    "FASTCHECK_GITHUB_ONLY_MARKER"
)

# Add a GitHub-run-authenticated operation to the existing AIR token ingress.
validation_pattern=re.compile(r"^\s*if\(body\.store!==c\.store\|\|.*BAD_CREDENTIALS.*$",re.M)
validation_line=(
    '    var stopLegacyTriggers = body.operation === "stop_legacy_fbs_triggers";\n'
    '    if(body.store!==c.store||! /^[0-9]{1,20}$/.test(runId)||!Number.isInteger(runAttempt)||runAttempt<1||!/^ghs_/.test(githubToken)||githubToken.length<20||githubToken.length>512||(!stopLegacyTriggers&&(wbToken.length<30||wbToken.length>4096))||(stopLegacyTriggers&&wbToken.length>0)) return wbFbsIngressRejectV1_("BAD_CREDENTIALS");'
)
validation,validation_count=validation_pattern.subn(validation_line,ingress)
if validation_count == 1:
    ingress=validation
elif validation_count == 0 and 'var stopLegacyTriggers = body.operation === "stop_legacy_fbs_triggers";' in ingress:
    pass
else:
    raise SystemExit("AIR_PATCH_INGRESS_CREDENTIAL_GUARD_UNEXPECTED_"+str(validation_count))

old_path='run.path!==c.workflowPath||run.head_branch!=="mainggg"'
new_path='(run.path!==c.workflowPath&&run.path!==".github/workflows/repair-air-fbs-trigger-loop.yml")||run.head_branch!=="mainggg"'
if old_path in ingress:
    ingress=replace_once(ingress,old_path,new_path,"ALLOW_GITHUB_TRIGGER_REPAIR_WORKFLOW")
elif new_path not in ingress:
    raise SystemExit("AIR_PATCH_INGRESS_WORKFLOW_PATH_GUARD_MISSING")

old_dispatch='    var props=PropertiesService.getScriptProperties(),runKey=runId+":"+runAttempt,last=props.getProperty("WB_FBS_GITHUB_INGRESS_LAST_RUN_V1");'
new_dispatch=(
    '    if(stopLegacyTriggers){\n'
    '      if(run.path!==".github/workflows/repair-air-fbs-trigger-loop.yml") return wbFbsIngressRejectV1_("GITHUB_RUN_MISMATCH");\n'
    '      return wbFbsIngressJsonV1_(wbFbsStopLegacyFbsTriggersV1_());\n'
    '    }\n'
    '    if(body.operation) return wbFbsIngressRejectV1_("BAD_OPERATION");\n'
    + old_dispatch
)
if old_dispatch in ingress:
    ingress=replace_once(ingress,old_dispatch,new_dispatch,"ADD_GITHUB_TRIGGER_REPAIR_OPERATION")
elif 'return wbFbsIngressJsonV1_(wbFbsStopLegacyFbsTriggersV1_());' not in ingress:
    raise SystemExit("AIR_PATCH_INGRESS_DISPATCH_ANCHOR_MISSING")

helper='''/* AIR_FBS_GITHUB_TRIGGER_REPAIR_V1 */
function wbFbsStopLegacyFbsTriggersV1_() {
  var before=ScriptApp.getProjectTriggers();
  var beforeHandlers=before.map(function(trigger){return trigger.getHandlerFunction();});
  var fastWasPending=isFastCheckPendingV293_();
  var fullWasPending=isFullSyncPendingV292_();
  clearFastCheckStateV293_(false);
  clearFullSyncStateV292_(false);
  var retired=new Set([
    'scheduledFastCheck',
    'scheduledFastCheckContinuationV293_',
    'scheduledFullSyncContinuationV292_'
  ]);
  var removed=[];
  before.forEach(function(trigger){
    var handler=trigger.getHandlerFunction();
    if(!retired.has(handler))return;
    ScriptApp.deleteTrigger(trigger);
    removed.push(handler);
  });
  var managed=ensureOperationalTriggersV361_(false);
  var after=ScriptApp.getProjectTriggers();
  var afterHandlers=after.map(function(trigger){return trigger.getHandlerFunction();});
  var remaining=afterHandlers.filter(function(handler){return retired.has(handler);});
  return {
    ok:remaining.length===0&&!isFastCheckPendingV293_()&&!isFullSyncPendingV292_(),
    action:'stop_legacy_fbs_triggers',
    store:'AIR',
    dataSheetsChanged:false,
    before:{handlers:beforeHandlers,fastQueuePending:fastWasPending,fullSyncQueuePending:fullWasPending},
    removed:removed,
    after:{handlers:afterHandlers,fastQueuePending:isFastCheckPendingV293_(),fullSyncQueuePending:isFullSyncPendingV292_()},
    remainingLegacyHandlers:remaining,
    retainedMaintenanceTrigger:afterHandlers.indexOf('scheduledDailyMaintenance')>=0,
    managedTriggers:managed
  };
}

'''
if "/* AIR_FBS_GITHUB_TRIGGER_REPAIR_V1 */" not in ingress:
    ingress=helper+ingress
timer_wrapper='''/* AIR_FBS_GITHUB_TRIGGER_TIMER_CLEANUP_V1 */
function wbFbsCleanupLegacyFbsTriggersFromTimerV1_() {
  var lock=LockService.getScriptLock();
  if(!lock.tryLock(5000))return {ok:false,error:'SCRIPT_LOCK_BUSY'};
  var result;
  try {
    result=wbFbsStopLegacyFbsTriggersV1_();
  } finally {
    lock.releaseLock();
  }
  try {
    log_('SUCCESS','AIR_FBS_TRIGGER_REPAIR','Старые локальные циклы ФБС отключены',result);
  } catch (error) {
    console.log(error);
  }
  return result;
}

'''
if "AIR_FBS_GITHUB_TRIGGER_TIMER_CLEANUP_V1" not in code:
    code=code.rstrip()+"\\n\\n"+timer_wrapper
code_path.write_text(code,encoding="utf-8")
ingress_path.write_text(ingress,encoding="utf-8")
for path in (code_path,ingress_path):
    print("PATCHED_FILE="+path.name)
    print("SHA256="+hashlib.sha256(path.read_bytes()).hexdigest())
print("AIR_FBS_GITHUB_ONLY_PATCH=ok")
