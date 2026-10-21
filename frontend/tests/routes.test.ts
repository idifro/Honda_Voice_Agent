import { decodeJwt } from 'jose';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Session state for the mocked cookie check; each test sets it.
const auth = vi.hoisted(() => ({ signedIn: false }));
vi.mock('@/lib/server/auth', () => ({
  requireSession: async () =>
    auth.signedIn ? null : Response.json({ error: 'Not signed in' }, { status: 401 }),
  hasSession: async () => auth.signedIn,
}));
vi.mock('@/lib/server/db', () => ({
  db: () => ({ query: async () => ({ rows: [] }) }),
}));

function post(url: string, body: unknown, ip = '198.51.100.1') {
  return new Request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-forwarded-for': ip },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  auth.signedIn = false;
  vi.resetModules(); // fresh rate limiters per test
});

describe('POST /api/login', () => {
  it('rejects a wrong passcode without setting a cookie', async () => {
    const { POST } = await import('@/app/api/login/route');
    const res = await POST(post('http://x/api/login', { passcode: 'wrong' }));
    expect(res.status).toBe(401);
    expect(res.headers.get('set-cookie')).toBeNull();
  });
  it('sets an HttpOnly, SameSite=Strict session cookie for the right passcode', async () => {
    const { POST } = await import('@/app/api/login/route');
    const res = await POST(post('http://x/api/login', { passcode: 'open-sesame-42' }));
    expect(res.status).toBe(200);
    const cookie = res.headers.get('set-cookie') ?? '';
    expect(cookie).toMatch(/^hv_session=/);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=strict/i);
  });
  it('blocks the 6th attempt in a minute from one IP (brute force)', async () => {
    const { POST } = await import('@/app/api/login/route');
    for (let i = 0; i < 5; i++) {
      expect(
        (await POST(post('http://x/api/login', { passcode: `guess${i}` }, '192.0.2.9'))).status
      ).toBe(401);
    }
    const blocked = await POST(
      post('http://x/api/login', { passcode: 'open-sesame-42' }, '192.0.2.9')
    );
    expect(blocked.status).toBe(429);
    expect(blocked.headers.get('retry-after')).toBeTruthy();
  });
});

describe('POST /api/token', () => {
  it('requires a session', async () => {
    const { POST } = await import('@/app/api/token/route');
    expect((await POST(post('http://x/api/token', {}))).status).toBe(401);
  });

  it('issues a one-room, microphone-only token that dispatches honda-sales, ignoring the body', async () => {
    auth.signedIn = true;
    const { POST } = await import('@/app/api/token/route');
    const evil = {
      room_name: 'someone-elses-room',
      participant_identity: 'agent-impersonator',
      room_config: { agents: [{ agent_name: 'other-agent' }] },
    };
    const res = await POST(post('http://x/api/token', evil));
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const body = await res.json();
    expect(body.roomName).toMatch(/^honda-[0-9a-f-]{36}$/);
    const claims = decodeJwt(body.participantToken) as Record<string, any>;
    expect(claims.sub).toMatch(/^caller-[0-9a-f-]{36}$/);
    expect(claims.video).toMatchObject({
      room: body.roomName,
      roomJoin: true,
      canPublish: true,
      canPublishData: false,
      canSubscribe: true,
    });
    expect(JSON.stringify(claims.video.canPublishSources).toLowerCase()).toContain('microphone');
    expect(claims.video.canPublishSources).toHaveLength(1);
    expect(claims.video.roomAdmin).toBeUndefined();
    expect(JSON.stringify(claims.roomConfig)).toContain('honda-sales');
    expect(JSON.stringify(claims.roomConfig)).not.toContain('other-agent');
    expect(claims.exp - claims.nbf).toBeLessThanOrEqual(600); // 10 minutes to join
  });

  it('rate-limits token requests', async () => {
    auth.signedIn = true;
    const { POST } = await import('@/app/api/token/route');
    for (let i = 0; i < 10; i++) {
      expect((await POST(post('http://x/api/token', {}, '192.0.2.50'))).status).toBe(200);
    }
    expect((await POST(post('http://x/api/token', {}, '192.0.2.50'))).status).toBe(429);
  });
});

describe('data routes', () => {
  it('require a session', async () => {
    const bookings = await import('@/app/api/bookings/route');
    const catalog = await import('@/app/api/catalog/route');
    expect((await bookings.GET()).status).toBe(401);
    expect((await catalog.GET()).status).toBe(401);
  });
  it('return rows when signed in', async () => {
    auth.signedIn = true;
    const bookings = await import('@/app/api/bookings/route');
    const res = await bookings.GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ bookings: [] });
  });
});

describe('leads and caller profile routes (SPEC §20.5)', () => {
  const room = 'honda-0b6f5a7e-2c1d-4e8f-9a3b-5c6d7e8f9a0b';
  it('require a session', async () => {
    const leads = await import('@/app/api/leads/route');
    const csv = await import('@/app/api/leads/export/route');
    const profile = await import('@/app/api/profile/route');
    expect((await leads.GET()).status).toBe(401);
    expect((await csv.GET()).status).toBe(401);
    expect((await profile.GET(new Request(`http://x/api/profile?room=${room}`))).status).toBe(401);
  });
  it('serve masked leads as JSON and as a CSV download', async () => {
    auth.signedIn = true;
    const leads = await import('@/app/api/leads/route');
    expect(await (await leads.GET()).json()).toEqual({ leads: [] });
    const { GET } = await import('@/app/api/leads/export/route');
    const res = await GET();
    expect(res.headers.get('content-type')).toMatch(/^text\/csv/);
    expect(res.headers.get('content-disposition')).toMatch(/^attachment; filename="honda-leads-/);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const header = (await res.text()).split('\r\n')[0];
    expect(header).toContain('phone_masked');
    expect(header).not.toMatch(/customer_phone|,phone,/); // only the masked number exists here
  });
  it('accept only token-route room names for the profile', async () => {
    auth.signedIn = true;
    const { GET } = await import('@/app/api/profile/route');
    for (const bad of ['', 'someone', "honda-x' or '1'='1", `${room}/../x`]) {
      const res = await GET(new Request(`http://x/api/profile?room=${encodeURIComponent(bad)}`));
      expect(res.status).toBe(400);
    }
    const ok = await GET(new Request(`http://x/api/profile?room=${room}`));
    expect(await ok.json()).toEqual({ profile: null });
  });
});
