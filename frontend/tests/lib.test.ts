import { describe, expect, it } from 'vitest';
import { passcodeMatches } from '@/lib/server/passcode';
import { RateLimiter, clientIp } from '@/lib/server/rate-limit';
import { createSessionToken, isValidSession, sessionCookieOptions } from '@/lib/server/session';

describe('passcode', () => {
  it('matches only the exact passcode', () => {
    expect(passcodeMatches('open-sesame-42', 'open-sesame-42')).toBe(true);
    expect(passcodeMatches('open-sesame-4', 'open-sesame-42')).toBe(false);
    expect(passcodeMatches('', 'open-sesame-42')).toBe(false);
    expect(passcodeMatches(undefined, 'open-sesame-42')).toBe(false);
    expect(passcodeMatches({ passcode: 'x' }, 'open-sesame-42')).toBe(false);
    expect(passcodeMatches('a'.repeat(201), 'a'.repeat(201))).toBe(false); // oversized input rejected
  });
});

describe('session', () => {
  const secret = 'y'.repeat(48);
  it('accepts its own tokens and rejects tampered or foreign ones', async () => {
    const token = await createSessionToken(secret);
    expect(await isValidSession(token, secret)).toBe(true);
    expect(await isValidSession(token, 'z'.repeat(48))).toBe(false); // other secret
    expect(await isValidSession(token.slice(0, -2) + 'aa', secret)).toBe(false); // tampered
    expect(await isValidSession(undefined, secret)).toBe(false);
    expect(await isValidSession('not-a-jwt', secret)).toBe(false);
  });
  it('rejects short secrets', async () => {
    await expect(createSessionToken('short')).rejects.toThrow();
  });
  it('cookie is HttpOnly and SameSite=Strict', () => {
    expect(sessionCookieOptions(true)).toMatchObject({
      httpOnly: true,
      sameSite: 'strict',
      secure: true,
    });
  });
});

describe('rate limiter', () => {
  it('allows `limit` hits per window, then blocks until it resets', () => {
    const rl = new RateLimiter(2, 1000);
    expect(rl.check('ip', 0).ok).toBe(true);
    expect(rl.check('ip', 10).ok).toBe(true);
    const third = rl.check('ip', 20);
    expect(third.ok).toBe(false);
    expect(third.retryAfterSeconds).toBe(1);
    expect(rl.check('other-ip', 20).ok).toBe(true); // per key
    expect(rl.check('ip', 1001).ok).toBe(true); // new window
  });
  it('reads the client IP from proxy headers', () => {
    const req = new Request('http://x', {
      headers: { 'x-forwarded-for': '203.0.113.7, 10.0.0.1' },
    });
    expect(clientIp(req)).toBe('203.0.113.7');
    expect(clientIp(new Request('http://x'))).toBe('local');
  });
});
