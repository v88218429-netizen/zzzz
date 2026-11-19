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
  smcmRunStage_("raw_api_write", function() {
    
    if (rawSh.getMaxColumns() < 20) throw new Error("WB_CONTENT_MANAGER_RAW_GRID_TOO_NARROW");
    rawData.forEach(function(row) {
      if (!Array.isArray(row) || row.length !== 20) throw new Error("WB_CONTENT_MANAGER_RAW_ROW_SHAPE");
      row.forEach(function(value) {
        if (typeof value === "string" && value.length > 50000) throw new Error("WB_CONTENT_MANAGER_RAW_CELL_SIZE_LIMIT");
      });
    });
    var rawLast = Math.min(rawSh.getLastRow(), 3000);
    var writeRows = rawData.slice();
    var existingRows = Math.max(0, rawLast - 1);
    while (writeRows.length < existingRows) writeRows.push(new Array(20).fill(""));
    if (writeRows.length) rawSh.getRange(2, 1, writeRows.length, 20).setValues(writeRows);
  });

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
    preview_ready: preview.good,
    preview_needs_review: preview.bad,
    snapshot: snapshot,
    readback: "pass"
  };
}

function smcmRunStage_(stage, fn) {
  try { return fn(); }
  catch (error) {
    var message = String(error && error.message || error || "").toLowerCase();
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
    if (code === "UNKNOWN" && stage.toLowerCase() === "raw_api_write") {
      try {
        var diagnosticSheet = SpreadsheetApp.openById("1--8PzvW7quVjolA5FmMS9tBNHA-IXGtFJyadWbt3eSA").getSheetByName("Полный API — данные");
        var sheetLocks = diagnosticSheet.getProtections(SpreadsheetApp.ProtectionType.SHEET);
        var rangeLocks = diagnosticSheet.getProtections(SpreadsheetApp.ProtectionType.RANGE);
        code = sheetLocks.length || rangeLocks.length ? "PROTECTION_PRESENT" : "SETVALUES_FAILURE";
      } catch (diagnosticError) { code = "DIAGNOSTIC_FAILURE"; }
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
    var photos = Array.isArray(card.photos) ? card.photos : [];
    var photo = photos.length ? (photos[0] || {}) : {};
    var image = photo.c246x328 || photo.big || photo.hq || photo.square || "";
    formulas.push([image ? '=IMAGE("' + String(image).replace(/"/g, '""') + '";4;100;75)' : ""]);
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

function smcmUpdatePreview_(mass, cards, attrs, docs) {
  var params = mass.getRange("B6:B17").getDisplayValues().map(function(row) { return String(row[0] || "").trim(); });
  var shopFilter = params[0] || "", categoryFilter = params[1] || "";
  var operation = params[2] || "Характеристика";
  var attrId = params[3] || "", attrName = params[4] || "", newValue = params[5] || "";
  var docType = params[6] || "", docNumber = params[7] || "", docStart = params[8] || "", docEnd = params[9] || "", docAction = params[10] || "";
  var selected = cards.filter(function(row) {
    if (row[0] !== true) return false;
    if (shopFilter && shopFilter !== "Все магазины" && String(row[2]) !== shopFilter) return false;
    if (categoryFilter && categoryFilter !== "Все категории" && String(row[6]) !== categoryFilter) return false;
    return true;
  });
  var output = [], good = 0, bad = 0;
  selected.forEach(function(card) {
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
  });
  mass.getRange("B18").setFormula("=COUNTIF('Карточки WB'!A2:A3000;TRUE)");
  mass.getRange("B19:B20").setValues([[good], [bad]]);
  mass.getRange("B21").setValue(selected.length ? "Предпросмотр обновлён. Отправка в WB отключена." : "Отметь карточки на листе «Карточки WB».");
  var last = Math.max(mass.getLastRow(), 24);
  mass.getRange(24, 1, last - 23, 9).clearContent();
  if (output.length) mass.getRange(24, 1, output.length, 9).setValues(output);
  else mass.getRange("I24").setValue("Нет выбранных карточек");
  return { selected: selected.length, good: good, bad: bad };
}
