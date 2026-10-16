// POST /api/login {passcode}: the demo gate (SPEC §12). Sets the signed session cookie on success.
import { NextResponse } from 'next/server';
import { serverConfig } from '@/lib/server/config';
import { passcodeMatches } from '@/lib/server/passcode';
import { RateLimiter, clientIp, tooManyRequests } from '@/lib/server/rate-limit';
import { SESSION_COOKIE, createSessionToken, sessionCookieOptions } from '@/lib/server/session';

export const dynamic = 'force-dynamic';
const limiter = new RateLimiter(5, 60_000); // 5 tries a minute per IP: passcode guessing is impractical

export async function POST(req: Request) {
  const limit = limiter.check(clientIp(req));
  if (!limit.ok) return tooManyRequests(limit.retryAfterSeconds);

  const body = await req.json().catch(() => null);
  if (!passcodeMatches(body?.passcode, serverConfig.demoPasscode())) {
    return NextResponse.json(
      { error: 'Wrong passcode' },
      { status: 401, headers: { 'Cache-Control': 'no-store' } }
    );
  }
  const res = NextResponse.json({ ok: true }, { headers: { 'Cache-Control': 'no-store' } });
  res.cookies.set(
    SESSION_COOKIE,
    await createSessionToken(serverConfig.sessionSecret()),
    sessionCookieOptions(serverConfig.secureCookies())
  );
  return res;
}
