// GET /api/catalog: models and base ex-showroom prices for the side panel.
import { requireSession } from '@/lib/server/auth';
import { db } from '@/lib/server/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  const unauthorized = await requireSession();
  if (unauthorized) return unauthorized;
  const { rows } = await db().query(
    `select id, name, segment, status, fuel, price_available, price_min_inr, price_max_inr
       from frontend.catalog
      order by status = 'on_sale' desc, price_min_inr nulls last, name`
  );
  return Response.json({ models: rows }, { headers: { 'Cache-Control': 'private, max-age=60' } });
}
