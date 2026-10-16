// Read-only database access for the panels (SPEC §12, rule 9). DATABASE_URL must be the `frontend_reader`
// role from migration 004: it can read only the masked views in schema `frontend`.
//
// TLS: node-postgres doesn't encrypt unless told to (Phase 5 found it connecting in cleartext). With
// DATABASE_CA_CERT set it uses TLS and verifies Supabase's certificate chain and host name.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { Pool, type PoolConfig } from 'pg';
import { serverConfig } from './config';

let pool: Pool | undefined;

export function tlsConfig(caPath = process.env.DATABASE_CA_CERT): PoolConfig['ssl'] {
  if (!caPath) return false; // local Postgres without TLS (Phase 11) only
  return {
    ca: readFileSync(path.resolve(process.cwd(), caPath), 'utf8'),
    rejectUnauthorized: true,
  };
}

export function db(): Pool {
  pool ??= new Pool({
    connectionString: serverConfig.databaseUrl(),
    ssl: tlsConfig(),
    max: 2, // the role allows 3 connections; Supabase's session pooler caps the project at 15
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
    statement_timeout: 3_000,
  });
  return pool;
}
