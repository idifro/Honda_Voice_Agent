// GET /api/leads/export: the same masked callback list as a CSV file (SPEC §20.5), formula-safe.
import { requireSession } from '@/lib/server/auth';
import { db } from '@/lib/server/db';
import { LEAD_COLUMNS, toCsv } from '@/lib/server/leads';

export const dynamic = 'force-dynamic';

export async function GET() {
  const unauthorized = await requireSession();
  if (unauthorized) return unauthorized;
  const { rows } = await db().query(
    `select ${LEAD_COLUMNS} from frontend.leads
      order by score_value desc nulls last, created_at desc limit 1000`
  );
  const day = new Date().toISOString().slice(0, 10);
  return new Response(toCsv(rows), {
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': `attachment; filename="honda-leads-${day}.csv"`,
      'Cache-Control': 'no-store',
    },
  });
}
