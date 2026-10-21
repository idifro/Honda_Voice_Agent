// GET /api/leads: the callback list for the Leads panel (SPEC §20.5), hottest first, already masked by the
// database view (migration 005): first name only, phone as "90xxx xx345".
import { requireSession } from '@/lib/server/auth';
import { db } from '@/lib/server/db';
import { LEAD_COLUMNS } from '@/lib/server/leads';

export const dynamic = 'force-dynamic';

export async function GET() {
  const unauthorized = await requireSession();
  if (unauthorized) return unauthorized;
  const { rows } = await db().query(
    `select ${LEAD_COLUMNS}
       from frontend.leads
      order by score_value desc nulls last, created_at desc
      limit 100`
  );
  return Response.json({ leads: rows }, { headers: { 'Cache-Control': 'no-store' } });
}
