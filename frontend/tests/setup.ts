// Test-only values: never real credentials.
process.env.LIVEKIT_URL = 'wss://example.livekit.cloud';
process.env.LIVEKIT_API_KEY = 'APItestkey';
process.env.LIVEKIT_API_SECRET = 'test-secret-test-secret-test-secret-123';
process.env.DEMO_PASSCODE = 'open-sesame-42';
process.env.SESSION_SECRET = 'x'.repeat(48);
process.env.AGENT_NAME = 'honda-sales';
