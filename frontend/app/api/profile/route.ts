// GET /api/profile?room=honda-<uuid>: the live caller profile for the Caller profile panel (SPEC §20.5),
// categories only (no transcript, no name or number), from the masked view of migration 005.
import { requireSession } from '@/lib/server/auth';
import { db } from '@/lib/server/db';

export const dynamic = 'force-dynamic';

const ROOM = /^honda-[0-9a-f-]{36}$/; // the token route's room names; anything else is refused

export async function GET(req: Request) {
  const unauthorized = await requireSession();
  if (unauthorized) return unauthorized;
  const room = new URL(req.url).searchParams.get('room') ?? '';
  if (!ROOM.test(room)) return Response.json({ error: 'Bad room' }, { status: 400 });
  const { rows } = await db().query(
    `select outcome, max_purchase_intent, intent, use_case, rider, priorities, budget_band, timeline,
            experience, payment_interest, objections, models_discussed, models_suggested, updated_at
       from frontend.caller_profiles
      where room_name = $1`,
    [room]
  );
  return Response.json({ profile: rows[0] ?? null }, { headers: { 'Cache-Control': 'no-store' } });
}
