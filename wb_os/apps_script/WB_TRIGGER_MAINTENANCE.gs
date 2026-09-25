/**
 * WB OS / Trigger maintenance
 *
 * Purpose:
 * - self-heal obsolete installable triggers left by older Supplier builds;
 * - keep the current master clock intact;
 * - provide temporary compatibility entrypoints so stale triggers can execute
 *   once, delete themselves, and then disappear.
 *
 * This file intentionally does NOT touch the active Supplier onEdit/onChange
 * handlers, UO handlers, SPP monitor, WB order feed, or other module triggers.
 */

function wbOsCleanupKnownDeadTriggers_() {
  var staleHandlers = {
    supplierScheduledFullSync_: true,
    supplierScheduledRefresh_: true
  };

  var triggers = ScriptApp.getProjectTriggers();
  var deleted = [];

  for (var i = 0; i < triggers.length; i++) {
    var handler = triggers[i].getHandlerFunction();

    if (!staleHandlers[handler]) {
      continue;
    }

    try {
      ScriptApp.deleteTrigger(triggers[i]);
      deleted.push(handler);
    } catch (error) {
      Logger.log(
        'WB OS trigger cleanup failed for ' +
        handler +
        ': ' +
        String(error && error.message ? error.message : error)
      );
    }
  }

  if (deleted.length) {
    Logger.log(
      'WB OS: deleted dead triggers: ' + deleted.join(', ')
    );
  }

  return deleted;
}


function wbOsCleanupKnownDeadTriggersNow() {
  var deleted = wbOsCleanupKnownDeadTriggers_();

  try {
    if (
      typeof wbOsEnsureFinalAutomationTriggerAtomic_ ===
        'function'
    ) {
      wbOsEnsureFinalAutomationTriggerAtomic_();
    } else if (
      typeof ensureFinalAutomationTrigger_ ===
        'function'
    ) {
      ensureFinalAutomationTrigger_();
    }
  } catch (error) {
    Logger.log(
      'WB OS master ensure after trigger cleanup failed: ' +
      String(error && error.message ? error.message : error)
    );
  }

  return JSON.stringify({
    ok: true,
    deleted: deleted
  });
}


/**
 * Compatibility stubs for legacy time triggers.
 * When one of these old triggers fires, it cleans both stale handlers and
 * then ensures the master clock. On the next trigger-list refresh these
 * obsolete rows should disappear.
 */
function supplierScheduledFullSync_() {
  wbOsCleanupKnownDeadTriggersNow();
}


function supplierScheduledRefresh_() {
  wbOsCleanupKnownDeadTriggersNow();
}
