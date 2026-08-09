/**
 * Restringe qué dominios pueden llamar al endpoint del widget web,
 * usando la lista definida en WEBSITE_ALLOWED_ORIGINS (.env).
 */
function corsMiddleware(req, res, next) {
  const allowed = (process.env.WEBSITE_ALLOWED_ORIGINS || '')
    .split(',')
    .map((o) => o.trim())
    .filter(Boolean);

  const origin = req.headers.origin;
  if (origin && allowed.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
}

module.exports = corsMiddleware;
