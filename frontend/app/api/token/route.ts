// POST /api/token: a LiveKit access token for one new room, with our agent dispatched (SPEC §12).
//
// Replaces the template's route, which had no authentication and let the browser choose the room config
// (and so which agent is dispatched). Here everything is decided on the server: the request body is ignored.
import {
  AccessToken,
  RoomAgentDispatch,
  RoomConfiguration,
  TrackSource,
  type VideoGrant,
} from 'livekit-server-sdk';
import { randomUUID } from 'node:crypto';
import { requireSession } from '@/lib/server/auth';
import { serverConfig } from '@/lib/server/config';
import { RateLimiter, clientIp, tooManyRequests } from '@/lib/server/rate-limit';

export const dynamic = 'force-dynamic';
const limiter = new RateLimiter(10, 60_000); // each token starts a paid agent session
const TOKEN_TTL = '10m'; // only needed to join; a connected call isn't cut off when it expires

export async function POST(req: Request) {
  const unauthorized = await requireSession();
  if (unauthorized) return unauthorized;
  const limit = limiter.check(clientIp(req));
  if (!limit.ok) return tooManyRequests(limit.retryAfterSeconds);

  const roomName = `honda-${randomUUID()}`; // unguessable, one per call
  const identity = `caller-${randomUUID()}`;
  const at = new AccessToken(serverConfig.livekitApiKey(), serverConfig.livekitApiSecret(), {
    identity,
    name: 'Caller',
    ttl: TOKEN_TTL,
  });
  const grant: VideoGrant = {
    room: roomName, // this room only
    roomJoin: true,
    canPublish: true,
    canPublishSources: [TrackSource.MICROPHONE], // microphone only: no camera, screen share
    canPublishData: false, // no data or text messages to the agent
    canSubscribe: true,
    canUpdateOwnMetadata: false,
  };
  at.addGrant(grant);
  at.roomConfig = new RoomConfiguration({
    agents: [new RoomAgentDispatch({ agentName: serverConfig.agentName() })], // explicit dispatch
  });

  return Response.json(
    {
      serverUrl: serverConfig.livekitUrl(),
      roomName,
      participantName: 'Caller',
      participantToken: await at.toJwt(),
    },
    { headers: { 'Cache-Control': 'no-store' } }
  );
}
