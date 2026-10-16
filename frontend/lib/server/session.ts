// Demo login session (SPEC §12): a signed, short-lived JWT in an HttpOnly, SameSite=Strict cookie. It gates
// the token route and the data routes; it is not user identity, only "knew the demo passcode".
import { SignJWT, jwtVerify } from 'jose';
import { randomUUID } from 'node:crypto';

export const SESSION_COOKIE = 'hv_session';
export const SESSION_TTL_SECONDS = 2 * 60 * 60;
const AUDIENCE = 'honda-voice-demo';

function key(secret: string): Uint8Array {
  if (secret.length < 32) throw new Error('SESSION_SECRET must be at least 32 characters');
  return new TextEncoder().encode(secret);
}

export async function createSessionToken(secret: string): Promise<string> {
  return new SignJWT({ sid: randomUUID() })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setAudience(AUDIENCE)
    .setExpirationTime(`${SESSION_TTL_SECONDS}s`)
    .sign(key(secret));
}

export async function isValidSession(token: string | undefined, secret: string): Promise<boolean> {
  if (!token) return false;
  try {
    await jwtVerify(token, key(secret), { algorithms: ['HS256'], audience: AUDIENCE });
    return true;
  } catch {
    return false;
  }
}

export function sessionCookieOptions(secure: boolean) {
  return {
    httpOnly: true, // not readable by page scripts
    sameSite: 'strict' as const, // not sent on cross-site requests: no CSRF on the POST routes
    secure, // HTTPS only in production
    path: '/',
    maxAge: SESSION_TTL_SECONDS,
  };
}
