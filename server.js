// NatalCare India Map — static server with single-credential HTTP Basic Auth.
// One shared username/password (set via env). No accounts, no signup.

const path = require('path');
const express = require('express');
const compression = require('compression');

const app = express();
const PORT = process.env.PORT || 3000;

// --- Single shared credential (override in production via env vars) ---
const AUTH_USER = process.env.MAP_USER || 'vnls';
const AUTH_PASS = process.env.MAP_PASS || 'nutracare@1234';

// Constant-time-ish comparison to avoid trivial timing leaks.
function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function basicAuth(req, res, next) {
  if (process.env.MAP_NO_AUTH === '1') return next(); // local dev/preview only
  const header = req.headers.authorization || '';
  const [scheme, encoded] = header.split(' ');
  if (scheme === 'Basic' && encoded) {
    const decoded = Buffer.from(encoded, 'base64').toString('utf8');
    const idx = decoded.indexOf(':');
    const user = decoded.slice(0, idx);
    const pass = decoded.slice(idx + 1);
    if (safeEqual(user, AUTH_USER) && safeEqual(pass, AUTH_PASS)) return next();
  }
  res.set('WWW-Authenticate', 'Basic realm="Nutracare India Map", charset="UTF-8"');
  return res.status(401).send('Authentication required.');
}

app.use(compression());
app.use(basicAuth);

// Static app + data (both behind auth). No caching: data is refreshed by the pipeline
// and the app is small, so always revalidate to avoid serving stale layers.
const noCache = { etag: true, maxAge: 0, setHeaders: (res) => res.set('Cache-Control', 'no-cache') };
app.use('/', express.static(path.join(__dirname, 'public'), noCache));
app.use('/data', express.static(path.join(__dirname, 'data'), noCache));

app.get('/healthz', (req, res) => res.json({ ok: true }));

app.listen(PORT, () => {
  console.log(`NatalCare India Map running at http://localhost:${PORT}`);
  console.log(`Login: ${AUTH_USER} / ${AUTH_PASS}  (override with MAP_USER / MAP_PASS env vars)`);
});
