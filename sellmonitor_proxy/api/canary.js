export default async function handler(req, res) {
  const shop = String(req.query?.shop || "").trim().toUpperCase();
  const envByShop = {
    AA: "SELLMONITOR_USER_API_KEY_AA",
    YV: "SELLMONITOR_USER_API_KEY_YV",
  };
  const envName = envByShop[shop];
  if (!envName) {
    return res.status(404).json({ ok: false, error: "unknown_shop" });
  }
  const key = process.env[envName];
  if (!key) {
    return res.status(503).json({ ok: false, error: "key_not_configured", shop });
  }

  const upstream = await fetch("https://sellmonitor.com/api/plugin/user/current/", {
    method: "GET",
    headers: {
      "Api-key": key,
      "Accept": "application/json",
    },
    redirect: "manual",
  });

  const contentType = upstream.headers.get("content-type") || "";
  const body = await upstream.text();
  if (!contentType.toLowerCase().includes("json")) {
    return res.status(502).json({
      ok: false,
      error: "sellmonitor_non_json",
      shop,
      upstream_http: upstream.status,
      content_type: contentType,
      preview: body.slice(0, 220).replace(/\s+/g, " "),
    });
  }

  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch {
    return res.status(502).json({
      ok: false,
      error: "sellmonitor_json_parse_failed",
      shop,
      upstream_http: upstream.status,
    });
  }

  return res.status(upstream.status).json({
    ok: upstream.ok,
    shop,
    upstream_http: upstream.status,
    content_type: contentType,
    top_level_keys: parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? Object.keys(parsed).slice(0, 30)
      : [],
  });
}
