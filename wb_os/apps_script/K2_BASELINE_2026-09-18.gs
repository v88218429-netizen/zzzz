/**
 * ============================================================
 * К2 API → GOOGLE SHEETS → TELEGRAM
 * ============================================================
 *
 * Что делает скрипт:
 *
 * 1. Авторизуется на planner.k2ff.ru.
 * 2. Получает актуальные складские остатки через API.
 * 3. Полностью обновляет лист «Остатки к2».
 * 4. Ждёт пересчёта формул в листе «Сводная».
 * 5. Отправляет в Telegram только сводные товары.
 * 6. Не отправляет дочерние карточки и комплекты WB.
 * 7. Автоматически запускается каждые 10 минут.
 *
 * Необходимые Script Properties:
 *
 * K2_USERNAME
 * K2_PASSWORD
 * TELEGRAM_BOT_TOKEN
 * TELEGRAM_CHAT_ID
 *
 * Gmail и Google Drive API больше не используются.
 */


/* ============================================================
 * НАСТРОЙКИ
 * ============================================================
 */

var K2_CFG = {
  BASE_URL: 'https://planner.k2ff.ru',

  LOGIN_URL:
    'https://planner.k2ff.ru/api/auth/login',

  ITEMS_URL:
    'https://planner.k2ff.ru/api/warehouse/items',

  STOCK_SHEET: 'Остатки к2',
  SUMMARY_SHEET: 'Сводная',

  // На листе «Сводная» данные начинаются с 12-й строки.
  SUMMARY_FIRST_DATA_ROW: 12,

  // Читаем столбцы A:AR.
  SUMMARY_LAST_COLUMN: 44,

  // Повторное напоминание о потребности.
  REPEAT_ALERT_HOURS: 24,

  // Повтор одной технической ошибки.
  ERROR_REPEAT_HOURS: 6,

  // Ограничение длины сообщения Telegram.
  TELEGRAM_MESSAGE_LIMIT: 3800,

  TELEGRAM_STATE_PREFIX:
    'TG_SUMMARY_NEED_'
};


/* ============================================================
 * ГЛАВНАЯ СИНХРОНИЗАЦИЯ
 * ============================================================
 */

/**
 * Основная автоматическая функция.
 *
 * Именно её запускает триггер каждые 10 минут.
 */
function syncK2StocksAndNotify() {
  var lock =
    LockService.getScriptLock();

  if (!lock.tryLock(30000)) {
    Logger.log(
      'Синхронизация К2 уже выполняется.'
    );

    return;
  }

  try {
    var items =
      getK2WarehouseItems_();

    if (!items || !items.length) {
      throw new Error(
        'К2 вернул пустой список остатков. ' +
        'Таблица не была изменена.'
      );
    }

    writeK2StocksToSheet_(items);

    SpreadsheetApp.flush();

    /*
     * Даём формулам в «Сводной»
     * время на пересчёт.
     */
    Utilities.sleep(4000);

    checkStockNeedsAndNotifyTelegram();

    saveK2SyncSuccess_(items.length);

    Logger.log(
      'Синхронизация завершена. ' +
      'Получено позиций: ' +
      items.length
    );
  } catch (error) {
    Logger.log(
      error.stack || error.message
    );

    notifyK2ApiError_(error);

    throw error;
  } finally {
    lock.releaseLock();
  }
}


/**
 * Проверяет соединение с К2,
 * но ничего не записывает в таблицу.
 */
function testK2ApiConnection() {
  var items =
    getK2WarehouseItems_();

  if (!items || !items.length) {
    throw new Error(
      'К2 подключён, но список остатков пуст.'
    );
  }

  var firstItem =
    items[0] || {};

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'Получено позиций: ' +
      items.length +
      '. Первый товар: ' +
      String(firstItem.name || ''),
      'К2 API работает',
      10
    );

  Logger.log(
    JSON.stringify(
      {
        count: items.length,
        firstItem: firstItem
      },
      null,
      2
    )
  );
}


/**
 * Загружает остатки в таблицу,
 * но не отправляет Telegram.
 */
function syncK2StocksOnly() {
  var items =
    getK2WarehouseItems_();

  if (!items || !items.length) {
    throw new Error(
      'К2 вернул пустой список остатков.'
    );
  }

  writeK2StocksToSheet_(items);

  SpreadsheetApp.flush();

  saveK2SyncSuccess_(items.length);

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'Загружено позиций: ' +
      items.length,
      'Остатки К2 обновлены',
      8
    );
}


/* ============================================================
 * АВТОРИЗАЦИЯ И ПОЛУЧЕНИЕ ОСТАТКОВ
 * ============================================================
 */

/**
 * Получает список складских остатков.
 *
 * Сначала пробует сохранённую сессию.
 * Если она истекла — входит заново.
 */
function getK2WarehouseItems_() {
  var props =
    PropertiesService
      .getScriptProperties();

  var savedCookie =
    props.getProperty(
      'K2_SESSION_COOKIE'
    );

  var savedClientId =
    props.getProperty(
      'K2_CLIENT_ID'
    );

  /*
   * Сначала используем сохранённую сессию.
   */
  if (savedCookie && savedClientId) {
    var savedResponse =
      requestK2Items_(
        savedCookie,
        savedClientId
      );

    if (savedResponse.code === 200) {
      return parseK2Items_(
        savedResponse.body
      );
    }

    /*
     * Авторизация истекла.
     */
    if (
      savedResponse.code === 401 ||
      savedResponse.code === 403
    ) {
      clearK2Session_();
    } else {
      throw new Error(
        'Ошибка получения остатков К2: HTTP ' +
        savedResponse.code +
        '. Ответ: ' +
        safeResponseText_(
          savedResponse.body
        )
      );
    }
  }

  /*
   * Выполняем новый вход.
   */
  var session =
    loginK2_();

  var response =
    requestK2Items_(
      session.cookie,
      session.clientId
    );

  if (response.code !== 200) {
    throw new Error(
      'К2 не отдал остатки после входа: HTTP ' +
      response.code +
      '. Ответ: ' +
      safeResponseText_(
        response.body
      )
    );
  }

  return parseK2Items_(
    response.body
  );
}


/**
 * Авторизуется на сайте К2.
 */
function getK2CredentialsWithFallback_() {
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
    if (!store) {
      continue;
    }

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


function loginK2_() {
  var props =
    PropertiesService
      .getScriptProperties();

  var credentials =
    getK2CredentialsWithFallback_();

  var username = credentials.username;
  var password = credentials.password;

  if (!username) {
    throw new Error(
      'Не заполнено свойство K2_USERNAME.'
    );
  }

  if (!password) {
    throw new Error(
      'Не заполнено свойство K2_PASSWORD.'
    );
  }

  var response =
    UrlFetchApp.fetch(
      K2_CFG.LOGIN_URL,
      {
        method: 'post',

        contentType:
          'application/json',

        headers: {
          Accept:
            'application/json',

          Origin:
            K2_CFG.BASE_URL,

          Referer:
            K2_CFG.BASE_URL +
            '/login'
        },

        payload:
          JSON.stringify({
            username: username,
            password: password
          }),

        followRedirects: false,
        muteHttpExceptions: true
      }
    );

  var code =
    response.getResponseCode();

  var body =
    response.getContentText();

  if (code !== 200) {
    throw new Error(
      'Ошибка авторизации К2: HTTP ' +
      code +
      '. Ответ: ' +
      safeResponseText_(body)
    );
  }

  var json;

  try {
    json = JSON.parse(body);
  } catch (error) {
    throw new Error(
      'К2 вернул некорректный JSON ' +
      'при авторизации.'
    );
  }

  if (
    !json ||
    json.success !== true ||
    !json.user ||
    !json.user.clientId
  ) {
    throw new Error(
      'Авторизация К2 не подтверждена. ' +
      'Ответ: ' +
      safeResponseText_(body)
    );
  }

  var cookie =
    extractCookiesFromResponse_(
      response
    );

  if (!cookie) {
    throw new Error(
      'К2 не вернул cookie авторизации. ' +
      'Автономная сессия не создана.'
    );
  }

  var clientId =
    String(
      json.user.clientId
    );

  props.setProperty(
    'K2_SESSION_COOKIE',
    cookie
  );

  props.setProperty(
    'K2_CLIENT_ID',
    clientId
  );

  props.setProperty(
    'K2_LAST_LOGIN_AT',
    new Date().toISOString()
  );

  return {
    cookie: cookie,
    clientId: clientId
  };
}


/**
 * Запрашивает актуальные остатки.
 */
function requestK2Items_(
  cookie,
  clientId
) {
  var url =
    K2_CFG.ITEMS_URL +
    '?clientId=' +
    encodeURIComponent(clientId);

  var response =
    UrlFetchApp.fetch(
      url,
      {
        method: 'get',

        headers: {
          Accept:
            'application/json',

          Cookie:
            cookie,

          Origin:
            K2_CFG.BASE_URL,

          Referer:
            K2_CFG.BASE_URL +
            '/warehouse'
        },

        followRedirects: false,
        muteHttpExceptions: true
      }
    );

  return {
    code:
      response.getResponseCode(),

    body:
      response.getContentText()
  };
}


/**
 * Получает cookie из ответа авторизации.
 */
function extractCookiesFromResponse_(
  response
) {
  var headers =
    response.getAllHeaders();

  var rawCookies =
    headers['Set-Cookie'] ||
    headers['set-cookie'];

  if (!rawCookies) {
    return '';
  }

  var cookieHeaders =
    Array.isArray(rawCookies)
      ? rawCookies
      : [rawCookies];

  var cookiePairs = [];

  for (
    var i = 0;
    i < cookieHeaders.length;
    i++
  ) {
    var headerText =
      String(
        cookieHeaders[i] || ''
      );

    /*
     * Разделяем несколько cookies,
     * не ломая дату Expires.
     */
    var parts =
      headerText.split(
        /,(?=\s*[A-Za-z0-9_.-]+=)/
      );

    for (
      var j = 0;
      j < parts.length;
      j++
    ) {
      var cookiePair =
        String(parts[j] || '')
          .split(';')[0]
          .trim();

      if (
        cookiePair &&
        cookiePair.indexOf('=') !== -1
      ) {
        cookiePairs.push(
          cookiePair
        );
      }
    }
  }

  return cookiePairs.join('; ');
}


/**
 * Разбирает ответ с остатками.
 */
function parseK2Items_(body) {
  var json;

  try {
    json = JSON.parse(body);
  } catch (error) {
    throw new Error(
      'К2 вернул некорректный JSON остатков.'
    );
  }

  var items;

  if (
    json &&
    Array.isArray(json.items)
  ) {
    items = json.items;
  } else if (Array.isArray(json)) {
    items = json;
  } else {
    throw new Error(
      'В ответе К2 отсутствует массив items.'
    );
  }

  if (!items.length) {
    throw new Error(
      'К2 вернул пустой массив остатков.'
    );
  }

  return items;
}


/**
 * Удаляет сохранённую сессию.
 */
function clearK2Session_() {
  var props =
    PropertiesService
      .getScriptProperties();

  props.deleteProperty(
    'K2_SESSION_COOKIE'
  );

  props.deleteProperty(
    'K2_CLIENT_ID'
  );

  props.deleteProperty(
    'K2_LAST_LOGIN_AT'
  );
}


/**
 * Ручной сброс сессии К2.
 *
 * Используйте после смены пароля.
 */
function resetK2ApiSession() {
  clearK2Session_();

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'Сессия удалена. ' +
      'При следующем запуске К2 войдёт заново.',
      'К2',
      8
    );
}


/* ============================================================
 * ЗАПИСЬ ОСТАТКОВ В ТАБЛИЦУ
 * ============================================================
 */

/**
 * Полностью обновляет лист «Остатки к2».
 *
 * Структура:
 *
 * A — SKU
 * B — Название
 * C — Остаток
 * D — Резерв
 * E — Мин. остаток
 * F — Изменено в K2
 * G — Обновлено в таблице
 */
function writeK2StocksToSheet_(items) {
  var ss =
    SpreadsheetApp
      .getActiveSpreadsheet();

  var sheet =
    ss.getSheetByName(
      K2_CFG.STOCK_SHEET
    );

  if (!sheet) {
    sheet =
      ss.insertSheet(
        K2_CFG.STOCK_SHEET
      );
  }

  var headers = [
    'SKU',
    'Название',
    'Остаток',
    'Резерв',
    'Мин. остаток',
    'Изменено в K2',
    'Обновлено в таблице'
  ];

  var tableUpdatedAt = new Date();
  var output = [];

  for (
    var i = 0;
    i < items.length;
    i++
  ) {
    var item =
      items[i] || {};

    var sku =
      String(
        item.sku === null ||
        item.sku === undefined
          ? ''
          : item.sku
      ).trim();

    var name =
      String(
        item.name || ''
      ).trim();

    if (!sku && !name) {
      continue;
    }

    output.push([
      sku,
      name,
      apiNumber_(item.stock),
      apiNumber_(item.reserved),
      apiNumber_(item.minStock),
      String(
        item.updatedAt || ''
      ),
      tableUpdatedAt
    ]);
  }

  if (!output.length) {
    throw new Error(
      'После обработки ответа К2 ' +
      'не осталось строк для записи.'
    );
  }

  var rowsToClear =
    Math.max(
      sheet.getLastRow(),
      output.length + 1
    );

  /*
   * Очищаем только A:G.
   */
  if (rowsToClear > 0) {
    sheet
      .getRange(
        1,
        1,
        rowsToClear,
        headers.length
      )
      .clearContent();
  }

  sheet
    .getRange(
      1,
      1,
      1,
      headers.length
    )
    .setValues([headers])
    .setFontWeight('bold')
    .setBackground('#4285f4')
    .setFontColor('#ffffff');

  /*
   * Сначала задаём текстовый формат SKU,
   * чтобы 06338 не превратился в 6338.
   */
  sheet
    .getRange(
      2,
      1,
      output.length,
      1
    )
    .setNumberFormat('@');

  sheet
    .getRange(
      2,
      1,
      output.length,
      headers.length
    )
    .setValues(output);

  sheet
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

  sheet.setFrozenRows(1);

  var props =
    PropertiesService
      .getScriptProperties();

  props.setProperty(
    'K2_LAST_SYNC_AT',
    new Date().toISOString()
  );

  props.setProperty(
    'K2_LAST_ITEMS_COUNT',
    String(output.length)
  );
}


/* ============================================================
 * TELEGRAM: ПОТРЕБНОСТЬ В ЗАКАЗЕ
 * ============================================================
 */

/**
 * Автоматическая проверка новых потребностей.
 */
function checkStockNeedsAndNotifyTelegram() {
  processSummaryNeeds_(false);
}


/**
 * Ручная отправка всех текущих потребностей.
 */
function sendAllCurrentNeedsToTelegram() {
  processSummaryNeeds_(true);
}


/**
 * Читает лист «Сводная».
 *
 * В Telegram отправляются только строки:
 *
 * A заполнен;
 * C пустой;
 * AN больше нуля;
 * товар ещё не заказан.
 *
 * Если C заполнен — это конкретная карточка WB
 * или комплект, поэтому строка пропускается.
 */
function processSummaryNeeds_(
  forceSendAll
) {
  var ss =
    SpreadsheetApp
      .getActiveSpreadsheet();

  var sheet =
    ss.getSheetByName(
      K2_CFG.SUMMARY_SHEET
    );

  if (!sheet) {
    throw new Error(
      'Не найден лист «' +
      K2_CFG.SUMMARY_SHEET +
      '».'
    );
  }

  var lastRow =
    sheet.getLastRow();

  if (
    lastRow <
    K2_CFG.SUMMARY_FIRST_DATA_ROW
  ) {
    Logger.log(
      'В листе «Сводная» нет данных.'
    );

    return;
  }

  var numRows =
    lastRow -
    K2_CFG.SUMMARY_FIRST_DATA_ROW +
    1;

  var values =
    sheet
      .getRange(
        K2_CFG.SUMMARY_FIRST_DATA_ROW,
        1,
        numRows,
        K2_CFG.SUMMARY_LAST_COLUMN
      )
      .getDisplayValues();

  var props =
    PropertiesService
      .getScriptProperties();

  var alerts = [];
  var now = new Date();

  var summaryRowsChecked = 0;
  var childRowsSkipped = 0;

  for (
    var i = 0;
    i < values.length;
    i++
  ) {
    var row =
      values[i];

    // A — название сводного товара.
    var productName =
      String(
        row[0] || ''
      ).trim();

    // C — артикул WB дочерней карточки.
    var wbArticle =
      String(
        row[2] || ''
      ).trim();

    if (!productName) {
      continue;
    }

    /*
     * Карточки и комплекты WB пропускаем.
     */
    if (wbArticle !== '') {
      childRowsSkipped++;
      continue;
    }

    summaryRowsChecked++;

    // M — общий остаток на фулфилментах.
    var totalFfStock =
      parseNumber_(row[12]);

    // N — остаток К2.
    var k2Stock =
      parseNumber_(row[13]);

    // O — остаток Иваново.
    var ivanovoStock =
      parseNumber_(row[14]);

    // W — заказы WB.
    var wbOrders =
      parseNumber_(row[22]);

    // AC — заказы Ozon.
    var ozonOrders =
      parseNumber_(row[28]);

    // AK — оборачиваемость.
    var turnoverDays =
      parseNumber_(row[36]);

    // AM — статус заказа.
    var orderStatus =
      String(
        row[38] || ''
      ).trim();

    // AN — потребность.
    var need =
      parseNumber_(row[39]);

    // AO — точка заказа.
    var orderPoint =
      parseNumber_(row[40]);

    // AR — уже заказанное количество.
    var orderedQty =
      parseNumber_(row[43]);

    var stateKey =
      createNeedStateKey_(
        productName
      );

    var activeOrder =
      hasActiveOrder_(
        orderStatus,
        orderedQty
      );

    /*
     * Нет потребности или уже есть заказ.
     */
    if (
      need <= 0 ||
      activeOrder
    ) {
      props.deleteProperty(
        stateKey
      );

      continue;
    }

    var currentLevel =
      getNeedAlertLevel_(
        turnoverDays,
        totalFfStock,
        orderPoint
      );

    var previousState =
      props.getProperty(
        stateKey
      );

    var shouldSend =
      forceSendAll ||
      shouldSendNeedAlert_(
        previousState,
        currentLevel,
        need,
        totalFfStock,
        now
      );

    if (!shouldSend) {
      continue;
    }

    alerts.push(
      buildNeedAlertMessage_({
        productName:
          productName,

        totalFfStock:
          totalFfStock,

        k2Stock:
          k2Stock,

        ivanovoStock:
          ivanovoStock,

        wbOrders:
          wbOrders,

        ozonOrders:
          ozonOrders,

        turnoverDays:
          turnoverDays,

        orderPoint:
          orderPoint,

        need:
          Math.ceil(need),

        level:
          currentLevel
      })
    );

    props.setProperty(
      stateKey,
      JSON.stringify({
        productName:
          productName,

        level:
          currentLevel,

        need:
          Math.ceil(need),

        totalFfStock:
          totalFfStock,

        sentAt:
          now.toISOString()
      })
    );
  }

  Logger.log(
    'Сводных строк: ' +
    summaryRowsChecked +
    '; карточек пропущено: ' +
    childRowsSkipped +
    '; уведомлений: ' +
    alerts.length
  );

  if (!alerts.length) {
    Logger.log(
      'Новых потребностей для Telegram нет.'
    );

    return;
  }

  sendTelegramAlertsInChunks_(
    alerts
  );
}


/**
 * Формирует сообщение по одному сводному товару.
 *
 * Название берётся ровно из столбца A.
 */
function buildNeedAlertMessage_(
  item
) {
  var emoji =
    getNeedAlertEmoji_(
      item.level
    );

  var totalOrders =
    item.wbOrders +
    item.ozonOrders;

  return (
    emoji +
    ' <b>' +
    escapeTelegramHtml_(
      item.productName
    ) +
    '</b>\n' +

    'Остатки: К2 <b>' +
    formatNumber_(
      item.k2Stock
    ) +
    '</b> · Иваново <b>' +
    formatNumber_(
      item.ivanovoStock
    ) +
    '</b> · всего <b>' +
    formatNumber_(
      item.totalFfStock
    ) +
    ' шт.</b>\n' +

    'Запаса: <b>' +
    formatDecimal_(
      item.turnoverDays
    ) +
    ' дн.</b> · точка заказа: <b>' +
    formatDecimal_(
      item.orderPoint
    ) +
    ' дн.</b>\n' +

    'Заказы: <b>' +
    formatNumber_(
      totalOrders
    ) +
    ' шт.</b> · WB ' +
    formatNumber_(
      item.wbOrders
    ) +
    ' · Ozon ' +
    formatNumber_(
      item.ozonOrders
    ) +
    '\n' +

    'Нужно заказать: <b>' +
    formatNumber_(
      item.need
    ) +
    ' шт.</b>'
  );
}


/**
 * Определяет уровень уведомления.
 */
function getNeedAlertLevel_(
  turnoverDays,
  totalFfStock,
  orderPoint
) {
  if (totalFfStock <= 0) {
    return 'OUT';
  }

  if (
    orderPoint > 0 &&
    turnoverDays <= orderPoint
  ) {
    return 'CRITICAL';
  }

  return 'PLAN';
}


function getNeedAlertEmoji_(
  level
) {
  if (level === 'OUT') {
    return '⚫';
  }

  if (level === 'CRITICAL') {
    return '🔴';
  }

  return '🟡';
}


/**
 * Определяет, оформлен ли уже заказ.
 */
function hasActiveOrder_(
  orderStatus,
  orderedQty
) {
  if (orderedQty > 0) {
    return true;
  }

  var normalized =
    String(
      orderStatus || ''
    )
      .toLowerCase()
      .replace(/ё/g, 'е')
      .trim();

  if (!normalized) {
    return false;
  }

  if (
    normalized.indexOf(
      'нет заказ'
    ) !== -1
  ) {
    return false;
  }

  return (
    normalized.indexOf(
      'заказ'
    ) !== -1
  );
}


/**
 * Определяет необходимость повторной отправки.
 */
function shouldSendNeedAlert_(
  previousJson,
  currentLevel,
  currentNeed,
  currentStock,
  now
) {
  if (!previousJson) {
    return true;
  }

  var previous;

  try {
    previous =
      JSON.parse(
        previousJson
      );
  } catch (error) {
    return true;
  }

  var levelWeight = {
    PLAN: 1,
    CRITICAL: 2,
    OUT: 3
  };

  var previousLevel =
    previous.level ||
    'PLAN';

  /*
   * Ситуация стала критичнее.
   */
  if (
    levelWeight[currentLevel] >
    levelWeight[previousLevel]
  ) {
    return true;
  }

  var previousNeed =
    Number(
      previous.need || 0
    );

  /*
   * Потребность выросла минимум на 10%,
   * но не менее чем на 5 штук.
   */
  var minimumNeedGrowth =
    Math.max(
      5,
      Math.ceil(
        previousNeed * 0.10
      )
    );

  if (
    currentNeed >=
    previousNeed +
    minimumNeedGrowth
  ) {
    return true;
  }

  var previousStock =
    Number(
      previous.totalFfStock || 0
    );

  /*
   * Общий остаток упал минимум на 20%.
   */
  if (
    previousStock > 0 &&
    currentStock <
    previousStock * 0.80
  ) {
    return true;
  }

  var sentAt =
    previous.sentAt
      ? new Date(
          previous.sentAt
        )
      : null;

  if (
    !sentAt ||
    isNaN(
      sentAt.getTime()
    )
  ) {
    return true;
  }

  var elapsedHours =
    (
      now.getTime() -
      sentAt.getTime()
    ) / 3600000;

  return (
    elapsedHours >=
    K2_CFG.REPEAT_ALERT_HOURS
  );
}


/**
 * Разбивает длинный список
 * на несколько сообщений Telegram.
 */
function sendTelegramAlertsInChunks_(
  alerts
) {
  var ss =
    SpreadsheetApp
      .getActiveSpreadsheet();

  var timezone =
    ss.getSpreadsheetTimeZone() ||
    'Europe/Moscow';

  var updatedAt =
    Utilities.formatDate(
      new Date(),
      timezone,
      'dd.MM.yyyy HH:mm'
    );

  var heading =
    '📦 <b>Потребность в заказе</b>\n' +
    'Обновлено: ' +
    updatedAt +
    '\n\n';

  var chunk =
    heading;

  for (
    var i = 0;
    i < alerts.length;
    i++
  ) {
    var block =
      (
        chunk === heading
          ? ''
          : '\n\n'
      ) +
      alerts[i];

    if (
      chunk.length +
      block.length >
      K2_CFG.TELEGRAM_MESSAGE_LIMIT
    ) {
      sendTelegramMessage_(
        chunk
      );

      chunk =
        heading +
        alerts[i];
    } else {
      chunk += block;
    }
  }

  if (chunk !== heading) {
    sendTelegramMessage_(
      chunk
    );
  }
}


/**
 * Отправляет сообщение в Telegram.
 */
function sendTelegramMessage_(
  text
) {
  var props =
    PropertiesService
      .getScriptProperties();

  var token =
    props.getProperty(
      'TELEGRAM_BOT_TOKEN'
    );

  var chatId =
    props.getProperty(
      'TELEGRAM_CHAT_ID'
    );

  if (!token || !chatId) {
    throw new Error(
      'Не заполнены TELEGRAM_BOT_TOKEN ' +
      'и TELEGRAM_CHAT_ID.'
    );
  }

  var response =
    UrlFetchApp.fetch(
      'https://api.telegram.org/bot' +
      token +
      '/sendMessage',
      {
        method: 'post',

        contentType:
          'application/json',

        payload:
          JSON.stringify({
            chat_id: chatId,
            text: text,
            parse_mode: 'HTML',
            disable_web_page_preview: true
          }),

        muteHttpExceptions: true
      }
    );

  var code =
    response.getResponseCode();

  var body =
    response.getContentText();

  if (
    code < 200 ||
    code >= 300
  ) {
    throw new Error(
      'Telegram API: HTTP ' +
      code +
      ' — ' +
      body.substring(0, 500)
    );
  }

  var json;

  try {
    json = JSON.parse(body);
  } catch (error) {
    throw new Error(
      'Telegram вернул некорректный ответ: ' +
      body.substring(0, 500)
    );
  }

  if (!json.ok) {
    throw new Error(
      'Telegram API: ' +
      body.substring(0, 500)
    );
  }
}


/* ============================================================
 * ПЕРВОНАЧАЛЬНАЯ НАСТРОЙКА И ТРИГГЕРЫ
 * ============================================================
 */

/**
 * Главная функция первоначальной настройки.
 *
 * После заполнения логина и пароля
 * запустите её один раз.
 *
 * Она:
 *
 * 1. Удалит старые Gmail-триггеры.
 * 2. Удалит старую сессию.
 * 3. Сбросит историю Telegram.
 * 4. Загрузит актуальные остатки.
 * 5. Отправит текущие потребности.
 * 6. Создаст запуск каждые 10 минут.
 */
function setupK2ApiAutomation() {
  deleteK2AutomationTriggers_();

  clearK2Session_();

  resetTelegramNeedNotificationsSilent_();

  syncK2StocksAndNotify();

  ScriptApp
    .newTrigger(
      'syncK2StocksAndNotify'
    )
    .timeBased()
    .everyMinutes(10)
    .create();

  try {
    sendTelegramMessage_(
      '✅ <b>Автоматизация К2 запущена</b>\n\n' +
      'Остатки будут обновляться каждые 10 минут.\n' +
      'Компьютер и таблицу можно закрывать.'
    );
  } catch (error) {
    Logger.log(
      'Не удалось отправить сообщение ' +
      'о запуске: ' +
      error.message
    );
  }

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'Автоматизация запущена. ' +
      'Проверка каждые 10 минут.',
      'К2',
      10
    );
}


/**
 * Создаёт триггер без сброса уведомлений.
 */
function createK2ApiTrigger() {
  deleteK2AutomationTriggers_();

  ScriptApp
    .newTrigger(
      'syncK2StocksAndNotify'
    )
    .timeBased()
    .everyMinutes(10)
    .create();

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'Триггер создан: каждые 10 минут',
      'К2',
      8
    );
}


/**
 * Удаляет старые и новые триггеры К2.
 */
function deleteK2AutomationTriggers_() {
  var triggers =
    ScriptApp.getProjectTriggers();

  var handlersToDelete = {
    processK2StockEmails: true,
    syncK2StocksAndNotify: true
  };

  var deleted = 0;

  for (
    var i = 0;
    i < triggers.length;
    i++
  ) {
    var handler =
      triggers[i]
        .getHandlerFunction();

    if (
      handlersToDelete[handler]
    ) {
      ScriptApp.deleteTrigger(
        triggers[i]
      );

      deleted++;
    }
  }

  Logger.log(
    'Удалено триггеров: ' +
    deleted
  );
}


/**
 * Ручное удаление всех триггеров К2.
 */
function deleteK2ApiTriggers() {
  deleteK2AutomationTriggers_();

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'Триггеры К2 удалены',
      'К2',
      6
    );
}


/* ============================================================
 * ТЕСТЫ И СБРОСЫ
 * ============================================================
 */

/**
 * Проверка Telegram.
 */
function testTelegram() {
  sendTelegramMessage_(
    '✅ Telegram подключён. ' +
    'Тестовая отправка работает.'
  );
}


/**
 * Сбрасывает историю уведомлений.
 */
function resetTelegramNeedNotifications() {
  var deleted =
    resetTelegramNeedNotificationsSilent_();

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'Сброшено уведомлений: ' +
      deleted,
      'Telegram',
      6
    );
}


/**
 * Внутренний сброс истории уведомлений.
 */
function resetTelegramNeedNotificationsSilent_() {
  var props =
    PropertiesService
      .getScriptProperties();

  var allProperties =
    props.getProperties();

  var deleted = 0;

  Object
    .keys(allProperties)
    .forEach(
      function(key) {
        var isCurrentState =
          key.indexOf(
            K2_CFG.TELEGRAM_STATE_PREFIX
          ) === 0;

        var isOldState =
          key.indexOf(
            'TG_NEED_'
          ) === 0;

        if (
          isCurrentState ||
          isOldState
        ) {
          props.deleteProperty(
            key
          );

          deleted++;
        }
      }
    );

  return deleted;
}


/* ============================================================
 * УВЕДОМЛЕНИЯ ОБ ОШИБКАХ
 * ============================================================
 */

/**
 * Отправляет ошибку К2,
 * но не чаще одного раза в 6 часов.
 */
function notifyK2ApiError_(
  error
) {
  var props =
    PropertiesService
      .getScriptProperties();

  var message =
    error && error.message
      ? error.message
      : String(error);

  var errorHash =
    createSimpleHash_(
      message
    );

  var previousHash =
    props.getProperty(
      'K2_LAST_ERROR_HASH'
    );

  var previousAt =
    props.getProperty(
      'K2_LAST_ERROR_AT'
    );

  var now =
    new Date();

  var shouldSend =
    true;

  if (
    previousHash === errorHash &&
    previousAt
  ) {
    var previousDate =
      new Date(previousAt);

    if (
      !isNaN(
        previousDate.getTime()
      )
    ) {
      var elapsedHours =
        (
          now.getTime() -
          previousDate.getTime()
        ) / 3600000;

      if (
        elapsedHours <
        K2_CFG.ERROR_REPEAT_HOURS
      ) {
        shouldSend = false;
      }
    }
  }

  props.setProperty(
    'K2_LAST_ERROR_HASH',
    errorHash
  );

  props.setProperty(
    'K2_LAST_ERROR_AT',
    now.toISOString()
  );

  props.setProperty(
    'K2_LAST_ERROR_MESSAGE',
    message
  );

  if (!shouldSend) {
    return;
  }

  try {
    sendTelegramMessage_(
      '⚠️ <b>Ошибка обновления остатков К2</b>\n\n' +
      '<code>' +
      escapeTelegramHtml_(
        message
      ) +
      '</code>\n\n' +
      'Последние корректные остатки ' +
      'в таблице сохранены.'
    );
  } catch (telegramError) {
    Logger.log(
      'Не удалось отправить ошибку: ' +
      telegramError.message
    );
  }
}


/**
 * Сохраняет успешный результат.
 */
function saveK2SyncSuccess_(
  itemsCount
) {
  var props =
    PropertiesService
      .getScriptProperties();

  var hadError =
    Boolean(
      props.getProperty(
        'K2_LAST_ERROR_HASH'
      )
    );

  props.setProperty(
    'K2_LAST_SUCCESS_AT',
    new Date().toISOString()
  );

  props.setProperty(
    'K2_LAST_SUCCESS_COUNT',
    String(itemsCount)
  );

  props.deleteProperty(
    'K2_LAST_ERROR_HASH'
  );

  props.deleteProperty(
    'K2_LAST_ERROR_AT'
  );

  props.deleteProperty(
    'K2_LAST_ERROR_MESSAGE'
  );

  /*
   * После восстановления отправляем
   * одно сообщение.
   */
  if (hadError) {
    try {
      sendTelegramMessage_(
        '✅ <b>К2 снова работает</b>\n\n' +
        'Остатки успешно обновлены.\n' +
        'Получено позиций: <b>' +
        itemsCount +
        '</b>.'
      );
    } catch (telegramError) {
      Logger.log(
        'Не удалось отправить сообщение ' +
        'о восстановлении: ' +
        telegramError.message
      );
    }
  }
}


/* ============================================================
 * ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
 * ============================================================
 */

function apiNumber_(
  value
) {
  if (
    value === null ||
    value === undefined ||
    value === ''
  ) {
    return 0;
  }

  var number =
    Number(
      String(value)
        .replace(/\u00A0/g, '')
        .replace(/\s/g, '')
        .replace(',', '.')
    );

  return isFinite(number)
    ? number
    : 0;
}


function parseNumber_(
  value
) {
  if (
    value === null ||
    value === undefined ||
    value === ''
  ) {
    return 0;
  }

  var cleaned =
    String(value)
      .replace(/\u00A0/g, '')
      .replace(/\s/g, '')
      .replace(',', '.')
      .replace(/[^\d.-]/g, '');

  if (!cleaned) {
    return 0;
  }

  var number =
    Number(cleaned);

  return isFinite(number)
    ? number
    : 0;
}


/**
 * Создаёт ключ состояния товара.
 */
function createNeedStateKey_(
  productName
) {
  var bytes =
    Utilities.computeDigest(
      Utilities
        .DigestAlgorithm
        .SHA_256,

      String(productName),

      Utilities.Charset.UTF_8
    );

  var hex =
    bytes
      .map(
        function(value) {
          var unsignedValue =
            value < 0
              ? value + 256
              : value;

          return (
            '0' +
            unsignedValue
              .toString(16)
          ).slice(-2);
        }
      )
      .join('');

  return (
    K2_CFG.TELEGRAM_STATE_PREFIX +
    hex
  );
}


function safeResponseText_(
  body
) {
  return String(
    body || ''
  )
    .replace(
      /"password"\s*:\s*"[^"]*"/gi,
      '"password":"<СКРЫТО>"'
    )
    .substring(0, 500);
}


function createSimpleHash_(
  value
) {
  var digest =
    Utilities.computeDigest(
      Utilities
        .DigestAlgorithm
        .SHA_256,

      String(value),

      Utilities.Charset.UTF_8
    );

  return Utilities
    .base64EncodeWebSafe(
      digest
    )
    .replace(/=+$/, '');
}


function escapeTelegramHtml_(
  value
) {
  return String(
    value || ''
  )
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}


function formatNumber_(
  value
) {
  return Math
    .round(
      Number(value) || 0
    )
    .toString()
    .replace(
      /\B(?=(\d{3})+(?!\d))/g,
      ' '
    );
}


function formatDecimal_(
  value
) {
  var number =
    Number(value) || 0;

  return number
    .toFixed(1)
    .replace('.', ',');
}
function saveK2Credentials() {
  var ui = SpreadsheetApp.getUi();

  var loginResult = ui.prompt(
    'Настройка К2',
    'Введите логин кабинета К2:',
    ui.ButtonSet.OK_CANCEL
  );

  if (
    loginResult.getSelectedButton() !==
    ui.Button.OK
  ) {
    return;
  }

  var username =
    loginResult.getResponseText().trim();

  if (!username) {
    ui.alert('Логин не введён.');
    return;
  }

  var passwordResult = ui.prompt(
    'Настройка К2',
    'Введите новый пароль кабинета К2:',
    ui.ButtonSet.OK_CANCEL
  );

  if (
    passwordResult.getSelectedButton() !==
    ui.Button.OK
  ) {
    return;
  }

  var password =
    passwordResult.getResponseText();

  if (!password) {
    ui.alert('Пароль не введён.');
    return;
  }

  PropertiesService
    .getScriptProperties()
    .setProperties({
      K2_USERNAME: username,
      K2_PASSWORD: password
    });

  // Удаляем старую сессию,
  // чтобы вход прошёл по новому паролю.
  clearK2Session_();

  ui.alert(
    'Готово',
    'Логин и пароль К2 сохранены.',
    ui.ButtonSet.OK
  );
}