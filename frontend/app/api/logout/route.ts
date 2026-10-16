import { NextResponse } from 'next/server';
import { serverConfig } from '@/lib/server/config';
import { SESSION_COOKIE, sessionCookieOptions } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

export async function POST() {
  const res = NextResponse.json({ ok: true }, { headers: { 'Cache-Control': 'no-store' } });
  res.cookies.set(SESSION_COOKIE, '', {
    ...sessionCookieOptions(serverConfig.secureCookies()),
    maxAge: 0,
  });
  return res;
}
