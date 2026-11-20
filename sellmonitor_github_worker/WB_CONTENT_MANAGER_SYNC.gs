/* WB_CONTENT_MANAGER_SYNC_V1 */
function sellmonitorGithubPublishContentManagerSnapshot_(body) {
  try {
  var targetId = "1--8PzvW7quVjolA5FmMS9tBNHA-IXGtFJyadWbt3eSA";
  if (String(body.spreadsheet_id || "") !== targetId) throw new Error("WB_CONTENT_MANAGER_TARGET_REJECTED");
  if (String(body.cabinet || "") !== "AP") throw new Error("WB_CONTENT_MANAGER_CABINET_REJECTED");
  // Store only the separate write token in project properties; never return or log it.
  var incomingWriteToken = String(body.write_token || "").trim();
  if (incomingWriteToken.length >= 30 && !/\s/.test(incomingWriteToken)) {
    PropertiesService.getScriptProperties().setProperty("WB_CONTENT_MANAGER_WRITE_TOKEN_AP", incomingWriteToken);
  }
  var cards = body.cards;
  if (!Array.isArray(cards) || !cards.length) throw new Error("WB_CONTENT_MANAGER_EMPTY_CARDS");
  if (cards.length > 2999) throw new Error("WB_CONTENT_MANAGER_CARD_LIMIT");

  var unique = {};
  cards.forEach(function(card) {
    var nm = String(card && (card.nmID || card.nmId) || "");
    if (nm) unique[nm] = card;
  });
  cards = Object.keys(unique).map(function(nm) { return unique[nm]; });
  if (!cards.length) throw new Error("WB_CONTENT_MANAGER_NO_NMIDS");

  var snapshot = String(body.snapshot || new Date().toISOString());
  var ss = SpreadsheetApp.openById(targetId);
  if (ss.getName() !== "WB API Manager — карточки и массовое редактирование") throw new Error("WB_CONTENT_MANAGER_TITLE_MISMATCH");
  var cardsSh = ss.getSheetByName("Карточки WB");
  var attrsSh = ss.getSheetByName("Характеристики");
  var wideAttrsSh = ss.getSheetByName("Характеристики по карточкам");
  var docsSh = ss.getSheetByName("Документы");
  var rawViewSh = ss.getSheetByName("Полный API");
  var rawSh = ss.getSheetByName("Полный API — данные");
  if (!rawSh) rawSh = ss.insertSheet("Полный API — данные");
  if (!rawSh.isSheetHidden()) rawSh.hideSheet();
  var massSh = ss.getSheetByName("Массовая правка");
  var startSh = ss.getSheetByName("Старт");
  if (!cardsSh || !attrsSh || !wideAttrsSh || !docsSh || !rawViewSh || !rawSh || !massSh || !startSh) throw new Error("WB_CONTENT_MANAGER_REQUIRED_TAB_MISSING");
  var previewTrigger = smcmRunStage_("preview_trigger_setup", function() { return smcmEnsureContentManagerPreviewTrigger_(ss); });

  var existingCardRows = smcmReadBody_(cardsSh, 17);
  var oldIds = {};
  var selectedByNm = {};
  existingCardRows.forEach(function(row) {
    var nm = smcmId_(row[3]);
    if (nm) {
      oldIds[nm] = true;
      selectedByNm[nm] = row[0] === true;
    }
  });
  var freshIds = {};
  cards.forEach(function(card) {
    var nm = smcmId_(card.nmID || card.nmId);
    if (nm) freshIds[nm] = true;
  });
  var oldCount = Object.keys(oldIds).length;
  if (!oldCount) throw new Error("WB_CONTENT_MANAGER_NO_EXISTING_SNAPSHOT");
  var overlap = Object.keys(oldIds).filter(function(nm) { return !!freshIds[nm]; }).length;
  var minimumOverlap = Math.min(oldCount, Math.max(10, Math.ceil(oldCount * 0.5)));
  if (overlap < minimumOverlap) throw new Error("WB_CONTENT_MANAGER_CABINET_MISMATCH_" + overlap + "_OF_" + oldCount);

  var attrControls = smcmAttrControls_(smcmReadBody_(attrsSh, 11));
  var docControls = smcmDocControls_(smcmReadBody_(docsSh, 20));
  var cardData = smcmBuildCards_(cards, snapshot, selectedByNm);
  var attrData = smcmBuildAttrs_(cards, attrControls);
  var docData = smcmBuildDocs_(cards, docControls);
  var rawData = smcmBuildRaw_(cards, snapshot);
  if (Math.max(cardData.rows.length, attrData.length, docData.length, rawData.length) > 2999) {
    throw new Error("WB_CONTENT_MANAGER_ROW_LIMIT");
  }

  smcmRunStage_("cards_write", function() {
    smcmReplaceBody_(cardsSh, 17, cardData.rows);
    if (cardData.formulas.length) {
      cardsSh.getRange(2, 2, cardData.formulas.length, 1).setFormulas(cardData.formulas);
      cardsSh.setColumnWidth(2, 92);
      cardsSh.setRowHeights(2, cardData.formulas.length, 106);
    }
  });
  smcmRunStage_("attributes_write", function() { smcmReplaceBody_(attrsSh, 11, attrData); });
  smcmRunStage_("documents_write", function() {
    // Imported WB values can be numeric IDs/JSON; keep dropdowns only on editable control columns.
    var importedDocRows = Math.max(docData.length, 1);
    docsSh.getRange(2, 7, importedDocRows, 5).clearDataValidations();   // G:K are imported WB document fields
    docsSh.getRange(2, 17, importedDocRows, 2).clearDataValidations(); // Q:R are imported WB ID/date fields
    docsSh.getRange(2, 20, importedDocRows, 1).clearDataValidations(); // T contains imported JSON
    smcmReplaceBody_(docsSh, 20, docData);
  });
  var previousRawRows = Number(PropertiesService.getScriptProperties().getProperty("WB_CONTENT_MANAGER_RAW_ROW_COUNT_AP") || 0);
  var rawWriteRows = rawData.slice();
  var rawBlankRow = [];
  for (var rawColumn = 0; rawColumn < 20; rawColumn++) rawBlankRow.push("");
  while (rawWriteRows.length < previousRawRows) rawWriteRows.push(rawBlankRow.slice());
  for (var rawOffset = 0; rawOffset < rawWriteRows.length; rawOffset += 10) {
    var rawChunk = rawWriteRows.slice(rawOffset, rawOffset + 10);
    var rawChunkStart = rawOffset;
    smcmRunStage_("raw_api_write_chunk_" + (Math.floor(rawOffset / 10) + 1), function() {
      rawChunk.forEach(function(row) {
        if (!Array.isArray(row) || row.length !== 20) throw new Error("WB_CONTENT_MANAGER_RAW_ROW_SHAPE");
        row.forEach(function(value) {
          if (typeof value === "string" && value.length > 50000) throw new Error("WB_CONTENT_MANAGER_RAW_CELL_SIZE_LIMIT");
        });
      });
      rawSh.getRange(2 + rawChunkStart, 1, rawChunk.length, 20).setValues(rawChunk);
    });
  }
  smcmRunStage_("raw_count_save", function() { PropertiesService.getScriptProperties().setProperty("WB_CONTENT_MANAGER_RAW_ROW_COUNT_AP", String(rawData.length)); });
  var editableMatrix = smcmRunStage_("wide_matrix_write", function() {
    return smcmWriteWideAttributes_(ss, cards, wideAttrsSh);
  });

  var preview = smcmRunStage_("mass_preview", function() { return smcmUpdatePreview_(massSh, cardData.rows, attrData, docData); });
  var statusB = "Свежие данные: " + cardData.rows.length + " карточек · " + attrData.length + " характеристик · " + docData.length + " строк документов";
  smcmRunStage_("start_status", function() {
    startSh.getRange("B4").setValue(statusB);
    var writeConfigured = !!PropertiesService.getScriptProperties().getProperty("WB_CONTENT_MANAGER_WRITE_TOKEN_AP");
    startSh.getRange("C4").setValue("WB Content API · чтение и характеристики " + (writeConfigured ? "подключены" : "ключ записи ещё не передан") + " · Дом Саныча (AP) · " + snapshot);
    startSh.getRange("A5").setValue("Ручное обновление");
    startSh.getRange("B5").setFormula('=HYPERLINK("https://github.com/v88218429-netizen/zzzz/actions/workflows/wb-content-manager-sync.yml";"Открыть GitHub Actions")');
    startSh.getRange("C5").setValue("Автоматическое обновление каждые 6 часов");
    smcmEnsureCharacteristicEditorControls_(startSh, editableMatrix.draftsSheet);
  });

  var readbackResponse = smcmRunStage_("readback", function() {
    return Sheets.Spreadsheets.Values.get(ss.getId(), "'Карточки WB'!D2:D" + (cardData.rows.length + 1), {majorDimension: "ROWS", valueRenderOption: "FORMATTED_VALUE"});
  });
  var readback = readbackResponse && Array.isArray(readbackResponse.values) ? readbackResponse.values : [];
  var matches = smcmRunStage_("readback_compare", function() { return readback.filter(function(row, index) {
    return smcmId_(row[0]) === smcmId_(cards[index].nmID || cards[index].nmId);
  }).length; });
  if (matches !== cardData.rows.length) throw new Error("WB_CONTENT_MANAGER_CARD_READBACK_MISMATCH");

  return {
    ok: true,
    cabinet: "AP",
    cards: cardData.rows.length,
    characteristics: attrData.length,
    editable_characteristics_matrix: true,
    pending_characteristic_edits: editableMatrix.pending,
    document_rows: docData.length,
    matched_existing: overlap,
    existing_cards: oldCount,
    selected: preview.selected,
    preview_trigger_ready: previewTrigger && previewTrigger.ready === true,
    preview_trigger_created: previewTrigger && previewTrigger.created === true,
    preview_ready: preview.good,
    preview_needs_review: preview.bad,
    snapshot: snapshot,
    readback: "pass"
  };
  } catch (error) {
    var rawMessage = String(error && error.message || error || "");
    var match = rawMessage.match(/WB_CONTENT_MANAGER_[A-Z0-9_]{1,80}/);
    var code = match ? match[0] : "WB_CONTENT_MANAGER_SYNC_UNKNOWN";
    Logger.log("WB_CONTENT_MANAGER_SYNC_FAILED_" + code);
    return {ok: false, error: code};
  }
}

function smcmRunStage_(stage, fn) {
  try { return fn(); }
  catch (error) {
    var rawMessage = String(error && error.message || error || "");
    if (rawMessage.indexOf("WB_CONTENT_MANAGER_PREVIEW_STEP_") === 0) throw error;
    var message = rawMessage.toLowerCase();
    var code = "UNKNOWN";
    if (/raw_cell_size_limit|50,?000|maximum.*character|cell.*character limit|exceeds.*characters|too many characters/.test(message)) code = "CELL_SIZE_LIMIT";
    else if (/raw_row_shape|number of columns|column count|columns in the data|row.*shape/.test(message)) code = "DATA_SHAPE";
    else if (/data validation|violates data validation/.test(message)) code = "DATA_VALIDATION";
    else if (/protected|permission|not allowed|not authorized|cannot edit|do not have permission/.test(message)) code = "PROTECTED_RANGE";
    else if (/grid|not enough columns|not enough rows|out of bounds|dimension|range.*outside/.test(message)) code = "GRID_SIZE";
    else if (/payload.*large|request.*too large|limit exceeded/.test(message)) code = "REQUEST_LIMIT";
    else if (/timed out|timeout|service spreadsheets|service.*failed|accessing document|internal error|try again later|operation could not be completed/.test(message)) code = "SHEETS_SERVICE";
    else if (/invalid argument|parameters.*signature|invalid range/.test(message)) code = "INVALID_ARGUMENT";
    else if (/null|undefined|not a function|cannot read|cannot set|array/.test(message)) code = "TYPE_OR_SHAPE";
    if (code === "UNKNOWN" && stage.toLowerCase().indexOf("raw_api_write") === 0) {
      try {
        var diagnosticSheet = SpreadsheetApp.openById("1--8PzvW7quVjolA5FmMS9tBNHA-IXGtFJyadWbt3eSA").getSheetByName("Полный API — данные");
        var sheetLocks = diagnosticSheet.getProtections(SpreadsheetApp.ProtectionType.SHEET);
        var rangeLocks = diagnosticSheet.getProtections(SpreadsheetApp.ProtectionType.RANGE);
        code = sheetLocks.length || rangeLocks.length ? "PROTECTION_PRESENT" : "SETVALUES_FAILURE";
      } catch (diagnosticError) { code = "DIAGNOSTIC_FAILURE"; }
    }
    if (code === "UNKNOWN" && stage.toLowerCase().indexOf("raw_api_prepare") === 0) {
      var signatureParts = [];
      message.split(" ").forEach(function(word) {
        if (signatureParts.length >= 8 || word.indexOf("@") >= 0 || word.indexOf("/") >= 0) return;
        var token = word.replace(/[^a-z0-9]/g, "");
        if (token && token.length <= 24 && !/^[0-9]+$/.test(token)) signatureParts.push(token.toUpperCase());
      });
      var signature = signatureParts.join("_");
      code = signature ? "PREP_" + signature : "PREP_UNCLASSIFIED";
    }
    var type = String(error && error.name || "ERROR").replace(/[^A-Za-z0-9]/g, "_").toUpperCase().slice(0, 24);
    var stack = String(error && error.stack || "");
    var lineMatch = stack.match(/:(\d+):\d+/);
    var line = lineMatch ? "_LINE_" + lineMatch[1] : "";
    throw new Error("WB_CONTENT_MANAGER_STAGE_" + stage.toUpperCase() + "_" + code + "_" + type + line);
  }
}

function smcmReadBody_(sheet, width) {
  var last = Math.min(sheet.getLastRow(), 3000);
  return last > 1 ? sheet.getRange(2, 1, last - 1, width).getValues() : [];
}

function smcmReplaceBody_(sheet, width, rows) {
  var last = Math.min(sheet.getLastRow(), 3000);
  if (last > 1) sheet.getRange(2, 1, last - 1, width).clearContent();
  if (rows.length) sheet.getRange(2, 1, rows.length, width).setValues(rows);
}

function smcmId_(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" && isFinite(value)) return String(Math.floor(value));
  return String(value).trim();
}

function smcmText_(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map(smcmText_).filter(Boolean).join("; ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function smcmJson_(value) {
  try { return JSON.stringify(value === undefined ? null : value); }
  catch (ignored) { return ""; }
}

function smcmDocsObj_(value) {
  var d = value;
  if (typeof d === "string") {
    try { d = JSON.parse(d); } catch (ignored) { d = {}; }
  }
  if (Array.isArray(d)) return { items: d };
  if (!d || typeof d !== "object") return { items: [] };
  if (Array.isArray(d.items)) return d;
  if (d.documents && Array.isArray(d.documents.items)) return d.documents;
  if (Array.isArray(d.documents)) return { items: d.documents, overallVerdict: d.overallVerdict || {} };
  return { items: [], overallVerdict: d.overallVerdict || {} };
}

function smcmAttrControls_(rows) {
  var out = {};
  rows.forEach(function(row) {
    var nm = smcmId_(row[2]), id = smcmText_(row[6]);
    if (nm && id) out[nm + "|" + id] = { apply: row[0] === true, newValue: row[9] };
  });
  return out;
}

function smcmDocControls_(rows) {
  var out = {};
  rows.forEach(function(row) {
    var nm = smcmId_(row[2]);
    if (!nm) return;
    var id = smcmText_(row[16]), type = smcmText_(row[6]), number = smcmText_(row[7]);
    var value = {
      apply: row[0] === true,
      action: row[11], newType: row[12], newNumber: row[13],
      newStart: row[14], newEnd: row[15]
    };
    out[nm + "|" + (id || ("type:" + type + "|number:" + number))] = value;
    if (!type && !number) out[nm + "|none"] = value;
  });
  return out;
}

function smcmBuildCards_(cards, snapshot, selections) {
  var rows = [], formulas = [];
  cards.forEach(function(card) {
    var nm = smcmId_(card.nmID || card.nmId);
    var attrs = Array.isArray(card.characteristics) ? card.characteristics : [];
    var docs = smcmDocsObj_(card.documents).items || [];
    var sizes = Array.isArray(card.sizes) ? card.sizes : [];
    var barcodes = [];
    sizes.forEach(function(size) {
      if (size && Array.isArray(size.skus)) size.skus.forEach(function(sku) { barcodes.push(sku); });
    });
    var dimensions = card.dimensions || {};
    var image = String(card._smcm_image_url || "");
    if (!/^https:\/\/raw\.githubusercontent\.com\/v88218429-netizen\/zzzz\/mainggg\/wb-content-manager-images\/AP\/[0-9]+\.jpg$/.test(image)) image = "";
    formulas.push([image ? '=IMAGE("' + image.replace(/"/g, '""') + '";4;100;75)' : ""]);
    rows.push([
      selections[nm] === true, "", "Текущий кабинет по API-ключу", nm,
      smcmText_(card.vendorCode), smcmText_(card.title),
      smcmText_(card.subjectName || card.subject), smcmText_(card.brand),
      "size=" + sizes.length + "; barcodes=" + barcodes.join(", "),
      "Получено из WB Content API", smcmText_(card.updatedAt),
      "WB Content API / свежее чтение",
      "Выгружено: " + attrs.length + " характеристик, " + docs.length + " документов",
      "width=" + smcmText_(dimensions.width) + "; height=" + smcmText_(dimensions.height) +
        "; length=" + smcmText_(dimensions.length) + "; weightBrutto=" + smcmText_(dimensions.weightBrutto) +
        "; isValid=" + smcmText_(dimensions.isValid),
      smcmText_(card.needKiz), "Загружено: " + attrs.length, snapshot
    ]);
  });
  return { rows: rows, formulas: formulas };
}

function smcmBuildAttrs_(cards, controls) {
  var rows = [];
  cards.forEach(function(card) {
    var nm = smcmId_(card.nmID || card.nmId);
    var attrs = Array.isArray(card.characteristics) ? card.characteristics : [];
    attrs.forEach(function(attr) {
      attr = attr || {};
      var id = smcmText_(attr.id), control = controls[nm + "|" + id] || {};
      rows.push([
        control.apply === true, "Текущий кабинет по API-ключу", nm,
        smcmText_(card.vendorCode), smcmText_(card.title),
        smcmText_(card.subjectName || card.subject),
        id, smcmText_(attr.name), smcmText_(attr.value),
        control.newValue || "", "Текущее значение из WB Content API"
      ]);
    });
  });
  return rows;
}

function smcmBuildDocs_(cards, controls) {
  var rows = [];
  cards.forEach(function(card) {
    var nm = smcmId_(card.nmID || card.nmId);
    var docs = smcmDocsObj_(card.documents), items = docs.items.length ? docs.items : [{}];
    items.forEach(function(doc) {
      doc = doc || {};
      var id = smcmText_(doc.id), type = smcmText_(doc.type), number = smcmText_(doc.number);
      var key = nm + "|" + (id || ("type:" + type + "|number:" + number));
      var control = controls[key] || controls[nm + "|none"] || {};
      var verdict = doc.verdict && typeof doc.verdict === "object" ? doc.verdict : {};
      var verdictParts = [];
      if (verdict.verified !== undefined) verdictParts.push("проверен: " + smcmText_(verdict.verified));
      if (verdict.status !== undefined) verdictParts.push("статус: " + smcmText_(verdict.status));
      if (verdict.reason) verdictParts.push("причина: " + smcmText_(verdict.reason));
      rows.push([
        control.apply === true, "Текущий кабинет по API-ключу", nm,
        smcmText_(card.vendorCode), smcmText_(card.title),
        smcmText_(card.subjectName || card.subject),
        type, number, smcmText_(doc.startDate), smcmText_(doc.endDate),
        verdictParts.join("; "), control.action || "", control.newType || "",
        control.newNumber || "", control.newStart || "", control.newEnd || "",
        id, smcmText_(doc.createdAt),
        doc.isEndless === true ? "Да" : (doc.isEndless === false ? "Нет" : ""),
        smcmJson_(verdict)
      ]);
    });
  });
  return rows;
}

function smcmBuildRaw_(cards, snapshot) {
  return cards.map(function(card) {
    return [
      smcmText_(card.nmID || card.nmId), smcmText_(card.imtID),
      smcmText_(card.subjectID), smcmText_(card.subjectName || card.subject),
      smcmText_(card.vendorCode), smcmText_(card.brand), smcmText_(card.title),
      smcmText_(card.description), smcmText_(card.needKiz), smcmText_(card.isKiz),
      smcmJson_(card.dimensions || {}), smcmJson_(card.sizes || []),
      smcmJson_(card.characteristics || []), smcmJson_(card.photos || []),
      smcmJson_(smcmDocsObj_(card.documents)), smcmJson_(card.tags || []),
      smcmText_(card.video), smcmText_(card.createdAt), smcmText_(card.updatedAt), snapshot
    ];
  });
}


function smcmEnsureContentManagerPreviewTrigger_(ss) {
  var handler = "smcmContentManagerPreviewOnEdit_";
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === handler) return {ready: true, created: false};
  }
  ScriptApp.newTrigger(handler).forSpreadsheet(ss).onEdit().create();
  return {ready: true, created: true};
}

function smcmContentManagerPreviewOnEdit_(e) {
  if (!e || !e.range) return;
  var edited = e.range;
  var sheet = edited.getSheet();
  var name = sheet.getName();
  var startRow = edited.getRow(), endRow = edited.getLastRow();
  var startColumn = edited.getColumn(), endColumn = edited.getLastColumn();
  var matrixEdit = name === "Характеристики по карточкам" && endRow >= 2 && endColumn >= 6;
  var sendCommand = name === "Старт" && startRow === 7 && endRow === 7 && startColumn === 2 && endColumn === 2;
  var cardSelectionEdit = name === "Карточки WB" && startColumn <= 1 && endColumn >= 1 && endRow >= 2;
  var massControlEdit = name === "Массовая правка" && startColumn <= 2 && endColumn >= 2 && startRow <= 17 && endRow >= 6;
  if (!matrixEdit && !sendCommand && !cardSelectionEdit && !massControlEdit) return;

  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    var ss = e.source || sheet.getParent();
    if (sendCommand) {
      if (edited.getValue() !== true) return;
      edited.setValue(false);
      smcmSendPendingCharacteristics_(ss);
      return;
    }
    if (matrixEdit) {
      smcmCaptureWideCharacteristicEdits_(ss, edited);
      return;
    }

    var cardsSheet = ss.getSheetByName("Карточки WB");
    var attrsSheet = ss.getSheetByName("Характеристики");
    var docsSheet = ss.getSheetByName("Документы");
    var massSheet = ss.getSheetByName("Массовая правка");
    if (!cardsSheet || !attrsSheet || !docsSheet || !massSheet) return;
    smcmUpdatePreview_(
      massSheet,
      smcmReadBody_(cardsSheet, 17),
      smcmReadBody_(attrsSheet, 11),
      smcmReadBody_(docsSheet, 20)
    );
  } catch (error) {
    var type = String(error && error.name || "ERROR").replace(/[^A-Za-z0-9]/g, "_").toUpperCase().slice(0, 24);
    Logger.log("WB_CONTENT_MANAGER_EDITOR_ONEDIT_FAILED_" + type);
  } finally {
    lock.releaseLock();
  }
}

function smcmRunPreviewStep_(stage, fn) {
  try { return fn(); }
  catch (error) {
    var message = String(error && error.message || error || "").toLowerCase();
    var code = "UNKNOWN";
    if (/protected|permission|not allowed|not authorized|cannot edit|do not have permission/.test(message)) code = "PROTECTED_RANGE";
    else if (/data validation|violates data validation/.test(message)) code = "DATA_VALIDATION";
    else if (/merged/.test(message)) code = "MERGED_CELLS";
    else if (/grid|range|dimension|bounds|outside/.test(message)) code = "RANGE";
    else if (/timed out|timeout|service.*failed|internal error|try again later/.test(message)) code = "SHEETS_SERVICE";
    var safeWords = {exception:1,error:1,argument:1,arguments:1,parameters:1,signature:1,method:1,property:1,object:1,context:1,access:1,denied:1,permission:1,permissions:1,protected:1,range:1,ranges:1,dimension:1,dimensions:1,bounds:1,row:1,rows:1,column:1,columns:1,value:1,values:1,invalid:1,spreadsheet:1,sheet:1,service:1,failed:1,failure:1,timed:1,timeout:1,internal:1,quota:1,exceeded:1,limit:1,cannot:1,call:1,read:1,write:1,not:1,found:1,available:1,google:1,sheets:1,getvalues:1,getvalue:1,getdisplayvalues:1};
    if (code === "UNKNOWN") {
      var tokens = message.match(/[a-z][a-z0-9]{1,20}/g) || [];
      var signature = tokens.filter(function(token) { return safeWords[token]; }).slice(0, 5).join("_").toUpperCase();
      if (signature) code += "_" + signature;
    }
    var type = String(error && error.name || "ERROR").replace(/[^A-Za-z0-9]/g, "_").toUpperCase().slice(0, 24);
    var stack = String(error && error.stack || "");
    var lineMatch = stack.match(/:(\d+):\d+/);
    var line = lineMatch ? "_LINE_" + lineMatch[1] : "";
    throw new Error("WB_CONTENT_MANAGER_PREVIEW_STEP_" + stage + "_" + code + "_" + type + line);
  }
}


function smcmReadMassPreviewControls_(mass) {
  var spreadsheetId = mass.getParent().getId();
  var response = Sheets.Spreadsheets.Values.get(
    spreadsheetId,
    "'Массовая правка'!B6:B16",
    {majorDimension: "ROWS", valueRenderOption: "FORMATTED_VALUE"}
  );
  var rows = response && Array.isArray(response.values) ? response.values : [];
  var params = [];
  for (var i = 0; i < 11; i++) {
    var row = rows[i] || [];
    params.push(String(row[0] || "").trim());
  }
  return params;
}

function smcmUpdatePreview_(mass, cards, attrs, docs) {
  var params = smcmRunPreviewStep_("READ_CONTROLS", function() { return smcmReadMassPreviewControls_(mass); });
  var shopFilter = params[0] || "", categoryFilter = params[1] || "";
  var operation = params[2] || "Характеристика";
  var attrId = params[3] || "", attrName = params[4] || "", newValue = params[5] || "";
  var docType = params[6] || "", docNumber = params[7] || "", docStart = params[8] || "", docEnd = params[9] || "", docAction = params[10] || "";
  var selected = smcmRunPreviewStep_("SELECT_CARDS", function() { return cards.filter(function(row) {
    if (row[0] !== true) return false;
    if (shopFilter && shopFilter !== "Все магазины" && String(row[2]) !== shopFilter) return false;
    if (categoryFilter && categoryFilter !== "Все категории" && String(row[6]) !== categoryFilter) return false;
    return true;
  }); });
  var output = [], good = 0, bad = 0;
  smcmRunPreviewStep_("BUILD_ROWS", function() { selected.forEach(function(card) {
    var nm = smcmId_(card[3]), shop = String(card[2] || ""), vendor = String(card[4] || ""), title = String(card[5] || ""), category = String(card[6] || "");
    var field = "", before = "", after = "", reason = "";
    if (operation.toLowerCase().indexOf("характер") >= 0) {
      var match = attrs.find(function(attr) {
        return smcmId_(attr[2]) === nm &&
          ((attrId && String(attr[6]) === attrId) || (!attrId && attrName && String(attr[7]).toLowerCase() === attrName.toLowerCase()));
      });
      field = match ? (String(match[7]) + " [ID " + String(match[6]) + "]") : (attrName || attrId || "Характеристика");
      before = match ? String(match[8] || "") : "";
      after = newValue;
      if (!attrId && !attrName) reason = "Укажи ID или название характеристики";
      else if (!newValue) reason = "Не заполнено новое значение";
      else if (!match) reason = "Характеристика не найдена в загруженных данных этой карточки";
    } else {
      field = "Документ: тип " + (docType || "—") + ", номер " + (docNumber || "—");
      var existing = docs.filter(function(doc) {
        return smcmId_(doc[2]) === nm && (doc[6] || doc[7]);
      }).map(function(doc) { return "тип " + doc[6] + ", № " + doc[7]; });
      before = existing.length ? existing.slice(0, 3).join("; ") : "Документов в снимке нет";
      after = "тип " + (docType || "—") + ", № " + (docNumber || "—") +
        (docStart ? ", с " + docStart : "") + (docEnd ? ", до " + docEnd : "") + (docAction ? ", " + docAction : "");
      if (!docType || !docNumber) reason = "Укажи тип и номер документа";
    }
    var result = reason || "Подходит для предпросмотра; запись в WB отключена";
    output.push([shop, nm, (vendor ? vendor + " · " : "") + title, category, field, before, after, result, "НЕТ — запись в WB отключена"]);
    if (reason) bad++; else good++;
  }); });
  smcmRunPreviewStep_("B18_FORMULA", function() { mass.getRange("B18").setFormula("=COUNTIF('Карточки WB'!A2:A3000;TRUE)"); });
  smcmRunPreviewStep_("COUNTERS", function() { mass.getRange("B19:B20").setValues([[good], [bad]]); });
  smcmRunPreviewStep_("STATUS", function() { mass.getRange("B21").setValue(selected.length ? "Предпросмотр обновлён. Отправка в WB отключена." : "Отметь карточки на листе «Карточки WB»."); });
  var outputState = smcmRunPreviewStep_("READ_EXISTING_OUTPUT", function() {
    var response = Sheets.Spreadsheets.Values.get(mass.getParent().getId(), "'Массовая правка'!A24:I3000", {majorDimension: "ROWS", valueRenderOption: "FORMATTED_VALUE"});
    return response && Array.isArray(response.values) ? response.values : [];
  });
  var clearRows = Math.max(outputState.length, output.length);
  if (clearRows) smcmRunPreviewStep_("CLEAR_OUTPUT", function() { mass.getRange(24, 1, clearRows, 9).clearContent(); });
  if (output.length) smcmRunPreviewStep_("WRITE_OUTPUT", function() { mass.getRange(24, 1, output.length, 9).setValues(output); });
  else smcmRunPreviewStep_("EMPTY_OUTPUT", function() { mass.getRange("I24").setValue("Нет выбранных карточек"); });
  return { selected: selected.length, good: good, bad: bad };
}


/* Editable one-card-per-row characteristics matrix and explicit WB write queue. */
function smcmEnsureCharacteristicDraftSheet_(ss) {
  var sheet = ss.getSheetByName("Черновики характеристик");
  if (!sheet) sheet = ss.insertSheet("Черновики характеристик");
  if (sheet.getLastRow() < 1) {
    sheet.getRange(1, 1, 1, 12).setValues([[
      "Ключ", "Статус", "nmID", "Артикул продавца", "Название карточки",
      "Категория", "ID характеристики", "Характеристика",
      "Было в WB", "Новое значение", "Изменено", "Результат WB"
    ]]);
    sheet.getRange(1, 1, 1, 12).setFontWeight("bold").setWrap(true);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function smcmJsonCell_(value, fallback) {
  try {
    if (value && typeof value === "object") return value;
    if (typeof value === "string" && value.trim()) return JSON.parse(value);
  } catch (ignored) {}
  return fallback;
}

function smcmDraftValueParts_(value) {
  var text = String(value === null || value === undefined ? "" : value);
  if (!text.trim()) return [];
  return text.split(/\s*;\s*|\r?\n/).map(function(part) { return part.trim(); }).filter(function(part) { return part !== ""; });
}

function smcmDraftKey_(nm, id) {
  return smcmId_(nm) + "|" + smcmId_(id);
}

function smcmDraftIsActive_(status) {
  return ["Черновик", "Принято WB", "Ошибка"].indexOf(String(status || "")) >= 0;
}

function smcmUpdateDraftStatusesFromSnapshot_(drafts, cards) {
  var last = drafts.getLastRow();
  if (last < 2) return;
  var byNm = {};
  cards.forEach(function(card) {
    var nm = smcmId_(card.nmID || card.nmId);
    if (nm) byNm[nm] = card;
  });
  var range = drafts.getRange(2, 1, last - 1, 12);
  var rows = range.getValues();
  var changed = false;
  rows.forEach(function(row) {
    var status = String(row[1] || "");
    if (!smcmDraftIsActive_(status)) return;
    var card = byNm[smcmId_(row[2])];
    if (!card || !Array.isArray(card.characteristics)) return;
    var attr = card.characteristics.find(function(item) { return smcmId_(item && item.id) === smcmId_(row[6]); });
    if (!attr) return;
    if (smcmText_(attr.value) === String(row[9] === null || row[9] === undefined ? "" : row[9])) {
      row[1] = "Сверено";
      row[10] = new Date().toISOString();
      row[11] = "Новое значение подтверждено свежей выгрузкой WB";
      changed = true;
    }
  });
  if (changed) range.setValues(rows);
}

function smcmEnsureCharacteristicEditorControls_(start, drafts) {
  start.getRange("A7").setValue("Отправить черновики характеристик в WB");
  start.getRange("B7").insertCheckboxes();
  if (start.getRange("B7").getValue() === "") start.getRange("B7").setValue(false);
  start.getRange("C7").setValue("Измени ячейки на листе «Характеристики по карточкам», проверь очередь и поставь галочку здесь.");
  start.getRange("A8").setValue("Очередь правок");
  start.getRange("B8").setFormula('=HYPERLINK("#gid=' + drafts.getSheetId() + '";"Открыть черновики")');
  var hasWriteToken = !!PropertiesService.getScriptProperties().getProperty("WB_CONTENT_MANAGER_WRITE_TOKEN_AP");
  start.getRange("C8").setValue(hasWriteToken
    ? "Ключ записи получен из защищённой синхронизации; содержимое ключа не показывается."
    : "Ключ записи пока не пришёл из GitHub Actions.");
}

function smcmWriteWideAttributes_(ss, cards, matrix) {
  var drafts = smcmEnsureCharacteristicDraftSheet_(ss);
  smcmUpdateDraftStatusesFromSnapshot_(drafts, cards);

  var oldColumns = Math.max(5, matrix.getLastColumn());
  var oldHeader = matrix.getRange(1, 1, 1, oldColumns).getDisplayValues()[0];
  var names = [];
  var seen = {};
  oldHeader.slice(5).forEach(function(value) {
    var name = String(value || "").trim();
    if (name && !seen[name]) { seen[name] = true; names.push(name); }
  });
  cards.forEach(function(card) {
    (Array.isArray(card.characteristics) ? card.characteristics : []).forEach(function(attr) {
      var name = String(attr && attr.name || "").trim();
      if (name && !seen[name]) { seen[name] = true; names.push(name); }
    });
  });
  if (names.length + 5 > matrix.getMaxColumns()) {
    matrix.insertColumnsAfter(matrix.getMaxColumns(), names.length + 5 - matrix.getMaxColumns());
  }

  var draftByKey = {};
  var draftLast = drafts.getLastRow();
  if (draftLast > 1) {
    drafts.getRange(2, 1, draftLast - 1, 12).getValues().forEach(function(row) {
      if (smcmDraftIsActive_(row[1])) draftByKey[String(row[0])] = row;
    });
  }
  var values = [[
    "Магазин", "nmID", "Артикул продавца", "Название карточки", "Категория"
  ].concat(names)];
  cards.forEach(function(card) {
    var nm = smcmId_(card.nmID || card.nmId);
    var attrs = Array.isArray(card.characteristics) ? card.characteristics : [];
    var byName = {};
    attrs.forEach(function(attr) {
      var name = String(attr && attr.name || "").trim();
      if (!name) return;
      if (!byName[name]) byName[name] = [];
      byName[name].push(attr);
    });
    var row = [
      "Текущий кабинет по API-ключу", nm,
      smcmText_(card.vendorCode), smcmText_(card.title),
      smcmText_(card.subjectName || card.subject)
    ];
    names.forEach(function(name) {
      var matching = byName[name] || [];
      var display = matching.length ? smcmText_(matching[0].value) : "";
      if (matching.length === 1) {
        var draft = draftByKey[smcmDraftKey_(nm, matching[0].id)];
        if (draft) display = String(draft[9] === null || draft[9] === undefined ? "" : draft[9]);
      }
      row.push(display);
    });
    values.push(row);
  });

  matrix.clearContents();
  matrix.getRange(1, 1, values.length, values[0].length).setValues(values);
  matrix.setFrozenRows(1);
  matrix.setFrozenColumns(5);
  matrix.getRange(1, 1, 1, values[0].length).setWrap(true).setFontWeight("bold");
  if (values.length > 1 && names.length) {
    matrix.getRange(2, 6, values.length - 1, names.length).setWrap(true).setVerticalAlignment("top");
    matrix.getRange(1, 6, 1, names.length).setNotes([names.map(function() {
      return "Редактируемое значение. Несколько значений разделяй точкой с запятой. Черновик сохраняется отдельно.";
    })]);
  }
  var protections = matrix.getProtections(SpreadsheetApp.ProtectionType.RANGE);
  var metadataProtection = protections.find(function(p) {
    return p.getDescription() === "WB_CONTENT_MANAGER_METADATA_COLUMNS";
  });
  if (!metadataProtection) {
    metadataProtection = matrix.getRange("A:E").protect();
    metadataProtection.setDescription("WB_CONTENT_MANAGER_METADATA_COLUMNS");
    metadataProtection.setWarningOnly(true);
  }
  return {
    draftsSheet: drafts,
    pending: Object.keys(draftByKey).length,
    cards: cards.length,
    characteristics: names.length
  };
}

function smcmUpsertCharacteristicDraft_(drafts, record) {
  var key = smcmDraftKey_(record.nm, record.id);
  var last = drafts.getLastRow();
  var found = 0;
  if (last > 1) {
    var keys = drafts.getRange(2, 1, last - 1, 1).getDisplayValues();
    for (var i = 0; i < keys.length; i++) {
      if (String(keys[i][0]) === key) { found = i + 2; break; }
    }
  }
  var row = [[
    key, record.status, smcmId_(record.nm), record.vendor, record.title,
    record.category, smcmId_(record.id), record.name, record.before,
    record.after, new Date().toISOString(), record.result || ""
  ]];
  if (found) drafts.getRange(found, 1, 1, 12).setValues(row);
  else drafts.getRange(Math.max(2, last + 1), 1, 1, 12).setValues(row);
}

function smcmCaptureWideCharacteristicEdits_(ss, edited) {
  var matrix = edited.getSheet();
  var drafts = smcmEnsureCharacteristicDraftSheet_(ss);
  var raw = ss.getSheetByName("Полный API — данные");
  if (!raw) throw new Error("WB_CONTENT_MANAGER_RAW_SHEET_MISSING");
  var rawRows = smcmReadBody_(raw, 20);
  var rawByNm = {};
  rawRows.forEach(function(row) {
    var nm = smcmId_(row[0]);
    if (nm) rawByNm[nm] = row;
  });
  var editedValues = edited.getValues();
  var startRow = edited.getRow(), startCol = edited.getColumn();
  for (var r = 0; r < editedValues.length; r++) {
    var rowNumber = startRow + r;
    if (rowNumber < 2) continue;
    var nm = smcmId_(matrix.getRange(rowNumber, 2).getValue());
    var rawRow = rawByNm[nm];
    if (!nm || !rawRow) continue;
    var attrs = smcmJsonCell_(rawRow[12], []);
    if (!Array.isArray(attrs)) attrs = [];
    for (var c = 0; c < editedValues[r].length; c++) {
      var column = startCol + c;
      if (column < 6) continue;
      var name = String(matrix.getRange(1, column).getDisplayValue() || "").trim();
      if (!name) continue;
      var cell = matrix.getRange(rowNumber, column);
      var matches = attrs.filter(function(attr) { return String(attr && attr.name || "").trim() === name; });
      if (matches.length !== 1 || !matches[0].id) {
        cell.setNote(matches.length > 1
          ? "Не отправлено: у карточки несколько характеристик с этим названием."
          : "Не отправлено: этой характеристики нет в карточке WB.");
        continue;
      }
      var attr = matches[0];
      var before = smcmText_(attr.value);
      var after = String(editedValues[r][c] === null || editedValues[r][c] === undefined ? "" : editedValues[r][c]).trim();
      var isUnchanged = before === after;
      smcmUpsertCharacteristicDraft_(drafts, {
        nm: nm, id: attr.id, name: name,
        vendor: rawRow[4], title: rawRow[6], category: rawRow[3],
        before: before, after: after,
        status: isUnchanged ? "Отменено" : "Черновик",
        result: isUnchanged ? "Значение возвращено к текущему значению WB" : "Готово к отправке"
      });
      cell.setNote(isUnchanged
        ? "Изменение отменено: значение совпадает с последней выгрузкой WB."
        : "Черновик сохранён. Проверь лист «Черновики характеристик», затем включи отправку на листе «Старт».");
    }
  }
}

function smcmSetDraftStatus_(drafts, rowNumber, status, result) {
  drafts.getRange(rowNumber, 2).setValue(status);
  drafts.getRange(rowNumber, 11).setValue(new Date().toISOString());
  drafts.getRange(rowNumber, 12).setValue(String(result || "").slice(0, 500));
}

function smcmFetchFreshCardsForWrite_(token) {
  var endpoint = "https://content-api.wildberries.ru/content/v2/get/cards/list";
  var all = [];
  var cursor = {limit: 20};
  for (var page = 0; page < 200; page++) {
    var response = UrlFetchApp.fetch(endpoint, {
      method: "post",
      contentType: "application/json",
      headers: {Authorization: token},
      payload: JSON.stringify({settings: {cursor: cursor, filter: {withPhoto: -1}}}),
      muteHttpExceptions: true
    });
    var code = response.getResponseCode();
    if (code < 200 || code >= 300) throw new Error("WB_CONTENT_MANAGER_FRESH_READ_HTTP_" + code);
    var body = {};
    try { body = JSON.parse(response.getContentText() || "{}"); }
    catch (ignored) { throw new Error("WB_CONTENT_MANAGER_FRESH_READ_BAD_JSON"); }
    var batch = body.cards;
    if (batch === undefined && body.data && typeof body.data === "object") batch = body.data.cards;
    if (!Array.isArray(batch)) throw new Error("WB_CONTENT_MANAGER_FRESH_READ_NO_CARDS");
    batch.forEach(function(card) { if (card && typeof card === "object") all.push(card); });
    var next = body.cursor || (body.data && body.data.cursor) || {};
    if (batch.length < 20 || !next.updatedAt || next.nmID === undefined || next.nmID === null) break;
    cursor = {limit: 20, updatedAt: next.updatedAt, nmID: next.nmID};
    if (page < 199) Utilities.sleep(650);
  }
  if (!all.length) throw new Error("WB_CONTENT_MANAGER_FRESH_READ_EMPTY");
  var byNm = {};
  all.forEach(function(card) {
    var nm = smcmId_(card.nmID || card.nmId);
    if (nm) byNm[nm] = card;
  });
  return byNm;
}

function smcmWritePayloadFromFreshCard_(card, characteristics) {
  var payload = {
    nmID: Number(card.nmID || card.nmId),
    vendorCode: String(card.vendorCode || ""),
    kizMarked: card.isKiz === true,
    brand: String(card.brand || ""),
    title: String(card.title || ""),
    description: String(card.description || ""),
    dimensions: card.dimensions || {},
    characteristics: characteristics,
    sizes: Array.isArray(card.sizes) ? card.sizes : []
  };
  if (card.documents !== undefined) {
    var docs = smcmDocsObj_(card.documents);
    payload.documents = {items: Array.isArray(docs.items) ? docs.items : []};
  }
  return payload;
}

function smcmSendPendingCharacteristics_(ss) {
  var start = ss.getSheetByName("Старт");
  var drafts = smcmEnsureCharacteristicDraftSheet_(ss);
  var token = PropertiesService.getScriptProperties().getProperty("WB_CONTENT_MANAGER_WRITE_TOKEN_AP") || "";
  if (!token) {
    start.getRange("C7").setValue("Отправка не выполнена: ключ записи ещё не передан из GitHub Actions.");
    return;
  }
  var last = drafts.getLastRow();
  if (last < 2) {
    start.getRange("C7").setValue("Очередь пустая — сначала измени значение в матрице.");
    return;
  }
  var draftRows = drafts.getRange(2, 1, last - 1, 12).getValues();
  var grouped = {};
  draftRows.forEach(function(row, index) {
    if (["Черновик", "Ошибка"].indexOf(String(row[1] || "")) < 0) return;
    var nm = smcmId_(row[2]);
    if (!grouped[nm]) grouped[nm] = [];
    grouped[nm].push({row: row, rowNumber: index + 2});
  });
  var nmIds = Object.keys(grouped);
  if (!nmIds.length) {
    start.getRange("C7").setValue("Нет черновиков для отправки. Сверенные и принятые WB правки не отправляю повторно.");
    return;
  }

  var freshByNm;
  try {
    start.getRange("C7").setValue("Проверяю свежие карточки WB и готовлю только отмеченные характеристики…");
    freshByNm = smcmFetchFreshCardsForWrite_(token);
  } catch (error) {
    var readError = String(error && error.message || "WB_CONTENT_MANAGER_FRESH_READ_FAILED");
    var safeReadError = (readError.match(/WB_CONTENT_MANAGER_[A-Z0-9_]{1,80}/) || ["WB_CONTENT_MANAGER_FRESH_READ_FAILED"])[0];
    nmIds.forEach(function(nm) {
      grouped[nm].forEach(function(item) {
        smcmSetDraftStatus_(drafts, item.rowNumber, "Ошибка", "WB не дал перечитать карточки; ничего не отправлено (" + safeReadError + ").");
      });
    });
    start.getRange("C7").setValue("Отправка отменена: не удалось заново прочитать WB (" + safeReadError + ").");
    return;
  }

  var accepted = 0, failed = 0, acceptedChanges = 0;
  var endpoint = "https://content-api.wildberries.ru/content/v2/cards/update";
  for (var n = 0; n < nmIds.length; n++) {
    var nm = nmIds[n];
    var card = freshByNm[nm];
    var group = grouped[nm];
    if (!card || !Array.isArray(card.characteristics)) {
      group.forEach(function(item) {
        smcmSetDraftStatus_(drafts, item.rowNumber, "Ошибка", "Карточка не найдена в свежем чтении WB.");
      });
      failed++;
      continue;
    }

    var characteristics = JSON.parse(JSON.stringify(card.characteristics));
    var conflict = "";
    var changedItems = [];
    group.forEach(function(item) {
      var row = item.row;
      var found = characteristics.filter(function(attr) { return smcmId_(attr && attr.id) === smcmId_(row[6]); });
      if (found.length !== 1) {
        conflict = "ID характеристики не найден или повторяется в свежей карточке.";
        return;
      }
      var current = smcmText_(found[0].value);
      var before = String(row[8] === null || row[8] === undefined ? "" : row[8]);
      var after = String(row[9] === null || row[9] === undefined ? "" : row[9]);
      if (current === after) {
        smcmSetDraftStatus_(drafts, item.rowNumber, "Сверено", "Свежая карточка WB уже содержит это значение.");
        return;
      }
      if (current !== before) {
        conflict = "В WB значение изменилось после подготовки черновика. Обнови данные и проверь правку.";
        return;
      }
      found[0].value = smcmDraftValueParts_(after);
      changedItems.push(item);
    });
    if (conflict) {
      group.forEach(function(item) {
        if (String(item.row[1]) === "Черновик" || String(item.row[1]) === "Ошибка") {
          smcmSetDraftStatus_(drafts, item.rowNumber, "Ошибка", conflict);
        }
      });
      failed++;
      continue;
    }
    if (!changedItems.length) continue;

    var payloadCard = smcmWritePayloadFromFreshCard_(card, characteristics);
    try {
      var response = UrlFetchApp.fetch(endpoint, {
        method: "post",
        contentType: "application/json",
        headers: {Authorization: token},
        payload: JSON.stringify([payloadCard]),
        muteHttpExceptions: true
      });
      var httpCode = response.getResponseCode();
      var responseText = response.getContentText() || "";
      var responseObject = {};
      try { responseObject = responseText ? JSON.parse(responseText) : {}; } catch (ignored) {}
      var ok = httpCode >= 200 && httpCode < 300 && responseObject.error !== true;
      var errorText = String(responseObject.errorText || "").replace(/\b[A-Za-z0-9._-]{40,}\b/g, "[скрыто]").slice(0, 320);
      var message = ok
        ? "Запрос принят WB; значение сверится после следующей выгрузки."
        : "WB вернул HTTP " + httpCode + (errorText ? ": " + errorText : "");
      changedItems.forEach(function(item) {
        smcmSetDraftStatus_(drafts, item.rowNumber, ok ? "Принято WB" : "Ошибка", message);
      });
      if (ok) {
        accepted++;
        acceptedChanges += changedItems.length;
      } else {
        failed++;
        if (httpCode === 401 || httpCode === 403) {
          for (var rest = n + 1; rest < nmIds.length; rest++) {
            grouped[nmIds[rest]].forEach(function(item) {
              smcmSetDraftStatus_(drafts, item.rowNumber, "Ошибка", "Не отправлено: WB отклонил ключ или нет прав Контент — чтение и запись.");
            });
          }
          failed += nmIds.length - n - 1;
          break;
        }
      }
    } catch (error) {
      changedItems.forEach(function(item) {
        smcmSetDraftStatus_(drafts, item.rowNumber, "Ошибка", "Ошибка соединения с WB: " + String(error && error.name || "ERROR"));
      });
      failed++;
    }
    if (n + 1 < nmIds.length) Utilities.sleep(650);
  }
  start.getRange("C7").setValue(
    "WB принял карточек: " + accepted + " · ошибок: " + failed +
    (acceptedChanges ? " · значения сверятся при следующей выгрузке" : "")
  );
}