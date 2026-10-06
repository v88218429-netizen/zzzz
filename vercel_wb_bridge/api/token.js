export default async function handler(req, res) {
  const key = req.headers["x-bridge-key"];
  if (!process.env.BRIDGE_KEY || key !== process.env.BRIDGE_KEY) {
    return res.status(401).json({ ok: false, error: "unauthorized" });
  }
  const profile = String(req.query.profile || "").toUpperCase();
  const map = { AP: "WB_API_TOKEN_AP", AA: "WB_API_TOKEN_AA", YV: "WB_API_TOKEN_YV" };
  const envKey = map[profile];
  if (!envKey) return res.status(400).json({ ok: false, error: "bad_profile" });
  const token = process.env[envKey];
  if (!token) return res.status(404).json({ ok: false, error: "token_missing" });
  res.setHeader("Cache-Control", "no-store");
  return res.status(200).json({ ok: true, profile, token });
}
