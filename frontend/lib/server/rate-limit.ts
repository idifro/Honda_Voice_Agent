// Fixed-window rate limiter, in memory (SPEC §12). Enough for one Next.js instance in a demo; several
// instances would need a shared store (e.g. Redis) instead.

export class RateLimiter {
  private hits = new Map<string, { count: number; resetAt: number }>();

  constructor(
    private readonly limit: number,
    private readonly windowMs: number
  ) {}

  /** Records a hit; `ok: false` once `limit` hits arrive within the window. */
  check(key: string, now = Date.now()): { ok: boolean; retryAfterSeconds: number } {
    const entry = this.hits.get(key);
    if (!entry || entry.resetAt <= now) {
      if (this.hits.size > 10_000) this.prune(now);
      this.hits.set(key, { count: 1, resetAt: now + this.windowMs });
      return { ok: true, retryAfterSeconds: 0 };
    }
    entry.count += 1;
    const retryAfterSeconds = Math.ceil((entry.resetAt - now) / 1000);
    return { ok: entry.count <= this.limit, retryAfterSeconds };
  }

  private prune(now: number) {
    for (const [k, v] of this.hits) if (v.resetAt <= now) this.hits.delete(k);
  }
}

/** Client IP for rate limiting. Behind a proxy, the first X-Forwarded-For entry; that header is only
 * trustworthy when a proxy you control sets it (Vercel and most hosts do). */
export function clientIp(req: Request): string {
  const forwarded = req.headers.get('x-forwarded-for');
  if (forwarded) return forwarded.split(',')[0].trim();
  return req.headers.get('x-real-ip') ?? 'local';
}

export function tooManyRequests(retryAfterSeconds: number): Response {
  return Response.json(
    { error: 'Too many attempts. Please wait and try again.' },
    {
      status: 429,
      headers: { 'Retry-After': String(retryAfterSeconds), 'Cache-Control': 'no-store' },
    }
  );
}
