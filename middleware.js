// Vercel Edge Middleware — single shared HTTP Basic Auth for the whole site.
// Credentials come from env vars MAP_USER / MAP_PASS (set them in the Vercel dashboard).
import { next } from '@vercel/edge';

export const config = { matcher: '/:path*' };

export default function middleware(req) {
  const USER = process.env.MAP_USER || 'vnls';
  const PASS = process.env.MAP_PASS || 'nutracare@1234';

  const header = req.headers.get('authorization') || '';
  const [scheme, encoded] = header.split(' ');
  if (scheme === 'Basic' && encoded) {
    let decoded = '';
    try { decoded = atob(encoded); } catch (e) { decoded = ''; }
    const i = decoded.indexOf(':');
    const user = decoded.slice(0, i);
    const pass = decoded.slice(i + 1);
    if (user === USER && pass === PASS) return next();
  }
  return new Response('Authentication required.', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Nutracare India Map", charset="UTF-8"',
      'Content-Type': 'text/plain',
    },
  });
}
