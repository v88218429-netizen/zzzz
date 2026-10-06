export default function handler(req, res) {
  res.status(200).json({
    ok: true,
    profiles: {
      AP: Boolean(process.env.WB_API_TOKEN_AP),
      AA: Boolean(process.env.WB_API_TOKEN_AA),
      YV: Boolean(process.env.WB_API_TOKEN_YV)
    }
  });
}
