/**
 * WB AI Manager — Google Sheets READ-ONLY bridge.
 *
 * 1) Create any Google Sheet -> Extensions -> Apps Script.
 * 2) Paste this whole file.
 * 3) Run setupBridge() once. It creates a secret in Script Properties.
 * 4) Deploy -> Web app -> Execute as: Me -> Access: Anyone, even anonymous.
 * 5) Put the /exec URL and generated secret into WB AI Manager / Railway.
 *
 * This code ONLY reads whitelisted spreadsheet IDs/ranges below. It has no write functions.
 */
function accessKey_() {
  const props = PropertiesService.getScriptProperties();
  let key = props.getProperty('ACCESS_KEY');
  if (!key) {
    key = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
    props.setProperty('ACCESS_KEY', key);
  }
  return key;
}

function setupBridge() {
  const key = accessKey_();
  console.log('WB_BRIDGE_KEY=' + key);
  return {ok: true, key_created: true};
}

const SOURCES = {
  weekly_summary: {
    spreadsheetId: '1hU24PrecF2hbeLbKfPEKRQbjXhdd8kONLTMsR4yNIug',
    ranges: {
      comparison: 'Сравнение!A1:AN160',
      air: 'AIR 09.09–16.09!A1:R120',
      sanych: 'Саныч 09.09–16.09!A1:R160',
      hozyushka: 'Хозяюшка 09.09–16.09!A1:R120'
    }
  },
  own_27: {
    spreadsheetId: '1VQwf-QPeSjexrEculDjWt_hpuCKu7PLzLZhMFjP2VZM',
    ranges: {
      summary: 'Сводная!A1:BI1200',
      unit_economics: 'Юнитка!A1:AQ500',
      ff_history: 'История остатков ФФ!A1:J800',
      products: 'Товары!A1:R500',
      orders_history: 'Заказы!A1:J40000',
      order_lifecycle: '_WB_ORDER_FEED!A1:L7000',
      supply_plan: '_ORDER_ANALYSIS_TMP!A1:L1000',
      ozon_cabinets: 'Ozon кабинеты!A1:X1200',
      ozon_cab2_products: 'Ozon каб2 товары!A1:L200',
      ozon_orders: 'Ozon Заказы!A1:V5000'
    }
  },
  sanych_sellmonitor: {
    spreadsheetId: '1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI',
    ranges: {
      dashboard: '00_Дашборд!A1:Z100',
      ads_status: '84_Статус_реклама!A1:H300',
      calculator: '05_Калькулятор!A1:BD300',
      stocks: '06_Остатки!A1:Z300',
      positions: '07_Контроль_позиций!A1:R6000'
    }
  },
  air_fbs: {
    spreadsheetId: '1qJvhEOIku7sOMydfrUz0PR5dkcU0sLjv433pa1MFBcM',
    ranges: {
      dashboard: 'Дашборд!A1:P80',
      orders: 'FBS Заказы!A1:AJ300'
    }
  },
  hozyushka_fbs: {
    spreadsheetId: '1f9dxXeZxkDth8h2C9GA7L7WqAr1-Ok-Oi5YtufLYOOk',
    ranges: {
      owner: '💼 Собственник!A1:P80',
      decisions: '🧠 Центр решений!A1:P100',
      analytics: '📈 Аналитика 360!A1:P100'
    }
  },
  dublinsky_legacy: {
    spreadsheetId: '1dG3nM93vsbmXXdrIYiCOwPDleg07sKoV0s45AYHDpB0',
    ranges: {
      calculator: 'калькулятор!A1:P100',
      rnp: 'РНП!A1:P200'
    }
  }
};

function doGet() {
  return json_({ok: true, service: 'WB AI Sheets Bridge', mode: 'READ_ONLY'});
}

function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (!body.key || body.key !== accessKey_()) return json_({ok: false, error: 'unauthorized'});
    const requested = body.action === 'all' ? Object.keys(SOURCES) : [String(body.source || '')];
    const out = {};
    requested.forEach(name => {
      if (!SOURCES[name]) return;
      out[name] = readSource_(name, SOURCES[name]);
    });
    return json_({ok: true, generated_at: new Date().toISOString(), sources: out});
  } catch (err) {
    return json_({ok: false, error: String(err && err.message ? err.message : err)});
  }
}

function readSource_(name, cfg) {
  const token = ScriptApp.getOAuthToken();
  const entries = Object.keys(cfg.ranges).map(key => {
    const a1 = cfg.ranges[key];
    const parsed = parseA1_(a1);
    return {
      key: key,
      a1: a1,
      url:
        'https://docs.google.com/spreadsheets/d/' +
        encodeURIComponent(cfg.spreadsheetId) +
        '/gviz/tq?tqx=out:csv&sheet=' +
        encodeURIComponent(parsed.sheet) +
        '&range=' +
        encodeURIComponent(parsed.range)
    };
  });

  const responses = UrlFetchApp.fetchAll(entries.map(entry => ({
    url: entry.url,
    method: 'get',
    headers: {Authorization: 'Bearer ' + token},
    muteHttpExceptions: true,
    followRedirects: true
  })));

  const ranges = {};
  responses.forEach((resp, i) => {
    const entry = entries[i];
    const code = resp.getResponseCode();
    const text = resp.getContentText();
    if (code >= 200 && code < 300) {
      ranges[entry.key] = {
        range: entry.a1,
        values: text ? Utilities.parseCsv(text) : []
      };
    } else {
      ranges[entry.key] = {
        range: entry.a1,
        error: 'GViz HTTP ' + code + ': ' + text.slice(0, 500)
      };
    }
  });

  return {
    name: name,
    title: name,
    spreadsheet_id: cfg.spreadsheetId,
    read_at: new Date().toISOString(),
    ranges: ranges
  };
}

function parseA1_(a1) {
  const bang = a1.indexOf('!');
  if (bang < 0) throw new Error('Range must include sheet name: ' + a1);
  let sheet = a1.substring(0, bang);
  const range = a1.substring(bang + 1);
  if (sheet.startsWith("'") && sheet.endsWith("'")) {
    sheet = sheet.slice(1, -1).replace(/''/g, "'");
  }
  return {sheet: sheet, range: range};
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
