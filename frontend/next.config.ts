import type { NextConfig } from 'next';

// Security headers (SPEC §12): no framing (clickjacking), no MIME sniffing, no referrer leakage, and only
// this page may use the microphone; camera and location are off.
const SECURITY_HEADERS = [
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'no-referrer' },
  { key: 'Permissions-Policy', value: 'microphone=(self), camera=(), geolocation=()' },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: '/:path*', headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
