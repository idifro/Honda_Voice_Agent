# Certificates (public, not secrets)

`supabase-prod-ca-2021.crt`: Supabase's public root CA ("Supabase Root 2021 CA", valid until 2031-04-26,
SHA-256 `80:70:25:AD:50:D4:ED:21:9D:2C:9C:7D:29:9C:00:4F:82:4E:B0:0C:F7:F6:5A:FE:F6:07:D0:7B:72:E6:CA:FA`).
Downloaded 2026-10-15 from Supabase's published location; the database pooler's certificate chain verifies
against it. Both the Python backend (`db/pool.py`) and the Next.js server (`lib/server/db.ts`) use it to
verify the database's TLS certificate and host name (SPEC §17), instead of encrypting without verification.
Set `DATABASE_CA_CERT=` (empty) for a local Postgres without TLS (Phase 11).
