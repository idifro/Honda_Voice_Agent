// Server-only settings (SPEC §12, rule 9). Read lazily so a missing value fails the request, not the build.
// Never import this from a client component: none of these may reach the browser.

function required(name: string, minLength = 1): string {
  const value = process.env[name];
  if (!value || value.length < minLength) {
    throw new Error(`${name} is not set${minLength > 1 ? ` (min ${minLength} characters)` : ''}`);
  }
  return value;
}

export const serverConfig = {
  livekitUrl: () => required('LIVEKIT_URL'),
  livekitApiKey: () => required('LIVEKIT_API_KEY'),
  livekitApiSecret: () => required('LIVEKIT_API_SECRET'),
  agentName: () => process.env.AGENT_NAME || 'honda-sales',
  databaseUrl: () => required('DATABASE_URL'),
  demoPasscode: () => required('DEMO_PASSCODE', 6),
  sessionSecret: () => required('SESSION_SECRET', 32),
  secureCookies: () => process.env.NODE_ENV === 'production',
};
