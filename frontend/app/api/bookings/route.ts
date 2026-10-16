// GET /api/bookings: today's and upcoming bookings for the panel, already masked by the database view
// (migration 004). Polled every 5 s by the panel.
import { requireSession } from '@/lib/server/auth';
import { db } from '@/lib/server/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  const unauthorized = await requireSession();
  if (unauthorized) return unauthorized;
  const { rows } = await db().query(
    `select reference, status, model, starts_at, slot_minutes, customer_display, phone_masked, language,
            licence_confirmed, calendar_sync_status, created_at
       from frontend.bookings
      order by starts_at, created_at
      limit 100`
  );
  return Response.json({ bookings: rows }, { headers: { 'Cache-Control': 'no-store' } });
}
