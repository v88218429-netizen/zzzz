/* WB_CONTENT_MANAGER_SYNC_V1 */
function sellmonitorGithubPublishContentManagerSnapshot_(body) {
  var targetId = "1--8PzvW7quVjolA5FmMS9tBNHA-IXGtFJyadWbt3eSA";
  if (String(body.spreadsheet_id || "") !== targetId) throw new Error("WB_CONTENT_MANAGER_TARGET_REJECTED");
  if (String(body.cabinet || "") !== "AP") throw new Error("WB_CONTENT_MANAGER_CABINET_REJECTED");
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
  var docsSh = ss.getSheetByName("Документы");
  var rawViewSh = ss.getSheetByName("Полный API");
  var rawSh = ss.getSheetByName("Полный API — данные");
  if (!rawSh) rawSh = ss.insertSheet("Полный API — данные");
  if (!rawSh.isSheetHidden()) rawSh.hideSheet();
  var massSh = ss.getSheetByName("Массовая правка");
  var startSh = ss.getSheetByName("Старт");
  if (!cardsSh || !attrsSh || !docsSh || !rawViewSh || !rawSh || !massSh || !startSh) throw new Error("WB_CONTENT_MANAGER_REQUIRED_TAB_MISSING");
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
    docsSh.getRange(2, 7, 2999, 1).clearDataValidations();
    docsSh.getRange(2, 20, 2999, 1).clearDataValidations();
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
  PropertiesService.getScriptProperties().setProperty("WB_CONTENT_MANAGER_RAW_ROW_COUNT_AP", String(rawData.length));

  var preview = smcmRunStage_("mass_preview", function() { return smcmUpdatePreview_(massSh, cardData.rows, attrData, docData); });
  var statusB = "Свежие данные: " + cardData.rows.length + " карточек · " + attrData.length + " характеристик · " + docData.length + " строк документов";
  smcmRunStage_("start_status", function() {
    startSh.getRange("B4").setValue(statusB);
    startSh.getRange("C4").setValue("WB Content API, только чтение · Дом Саныча (AP) · " + snapshot);
    startSh.getRange("A5").setValue("Ручное обновление");
    startSh.getRange("B5").setFormula('=HYPERLINK("https://github.com/v88218429-netizen/zzzz/actions/workflows/wb-content-manager-sync.yml";"Открыть GitHub Actions")');
    startSh.getRange("C5").setValue("Автоматическое обновление каждые 6 часов");
  });

  SpreadsheetApp.flush();
  var readback = smcmRunStage_("readback", function() { return cardsSh.getRange(2, 4, cardData.rows.length, 1).getDisplayValues(); });
  var matches = readback.filter(function(row, index) {
    return smcmId_(row[0]) === smcmId_(cards[index].nmID || cards[index].nmId);
  }).length;
  if (matches !== cardData.rows.length) throw new Error("WB_CONTENT_MANAGER_CARD_READBACK_MISMATCH");

  return {
    ok: true,
    cabinet: "AP",
    cards: cardData.rows.length,
    characteristics: attrData.length,
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
    throw new Error("WB_CONTENT_MANAGER_STAGE_" + stage.toUpperCase() + "_" + code);
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
  var cardSelectionEdit = name === "Карточки WB" && startColumn <= 1 && endColumn >= 1 && endRow >= 2;
  var massControlEdit = name === "Массовая правка" && startColumn <= 2 && endColumn >= 2 && startRow <= 17 && endRow >= 6;
  if (!cardSelectionEdit && !massControlEdit) return;

  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    var ss = e.source || sheet.getParent();
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
    SpreadsheetApp.flush();
  } catch (error) {
    var type = String(error && error.name || "ERROR").replace(/[^A-Za-z0-9]/g, "_").toUpperCase().slice(0, 24);
    Logger.log("WB_CONTENT_MANAGER_PREVIEW_ONEDIT_FAILED_" + type);
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
    var lineMatch = stack.match(/:(\\d+):\\d+/);
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
  var clearRows = 3000 - 23;
  smcmRunPreviewStep_("CLEAR_OUTPUT", function() { mass.getRange(24, 1, clearRows, 9).clearContent(); });
  if (output.length) smcmRunPreviewStep_("WRITE_OUTPUT", function() { mass.getRange(24, 1, output.length, 9).setValues(output); });
  else smcmRunPreviewStep_("EMPTY_OUTPUT", function() { mass.getRange("I24").setValue("Нет выбранных карточек"); });
  return { selected: selected.length, good: good, bad: bad };
}
