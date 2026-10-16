import { cookies } from 'next/headers';
import { serverConfig } from './config';
import { SESSION_COOKIE, isValidSession } from './session';

/** For server components: whether this browser has a valid demo session. */
export async function hasSession(): Promise<boolean> {
  const jar = await cookies();
  return isValidSession(jar.get(SESSION_COOKIE)?.value, serverConfig.sessionSecret());
}

/** For route handlers: a 401 response when there's no valid session, else null. */
export async function requireSession(): Promise<Response | null> {
  if (await hasSession()) return null;
  return Response.json(
    { error: 'Not signed in' },
    { status: 401, headers: { 'Cache-Control': 'no-store' } }
  );
}
