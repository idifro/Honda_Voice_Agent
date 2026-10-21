'use client';

import { useCallback, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { TokenSource } from 'livekit-client';
import { useSession } from '@livekit/components-react';
import { WarningIcon } from '@phosphor-icons/react/dist/ssr';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { StartAudioButton } from '@/components/agents-ui/start-audio-button';
import { LogoutButton } from '@/components/app/logout-button';
import { SidePanel } from '@/components/app/side-panel';
import { ViewController } from '@/components/app/view-controller';
import { Button } from '@/components/ui/button';
import { Toaster } from '@/components/ui/sonner';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { useDebugMode } from '@/hooks/useDebug';

const IN_DEVELOPMENT = process.env.NODE_ENV !== 'production';

function AppSetup() {
  useDebugMode({ enabled: IN_DEVELOPMENT });
  useAgentErrors();

  return null;
}

export function App() {
  const router = useRouter();
  // Our own route only: it checks the session cookie and decides room, identity and agent server-side.
  const tokenSource = useMemo(() => TokenSource.endpoint('/api/token'), []);
  const session = useSession(tokenSource);
  const [panelOpen, setPanelOpen] = useState(false); // drawer on small screens
  const onUnauthorized = useCallback(() => router.refresh(), [router]); // session expired: back to sign-in
  const roomName = session.isConnected ? session.room.name : null; // for the live caller profile

  return (
    <AgentSessionProvider session={session}>
      <AppSetup />
      <header className="bg-background/80 fixed inset-x-0 top-0 z-50 flex h-14 items-center gap-3 border-b px-4 backdrop-blur">
        <span className="text-foreground font-semibold">Honda voice assistant</span>
        <span className="text-muted-foreground hidden text-xs sm:inline">demo</span>
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            className="lg:hidden"
            aria-expanded={panelOpen}
            onClick={() => setPanelOpen((open) => !open)}
          >
            {panelOpen ? 'Close' : 'Panel'}
          </Button>
          <LogoutButton />
        </div>
      </header>
      <main className="grid h-svh grid-cols-1 place-content-center pt-14 lg:pr-[380px]">
        <ViewController />
      </main>
      <SidePanel
        onUnauthorized={onUnauthorized}
        roomName={roomName}
        className="fixed top-14 right-0 bottom-0 z-40 hidden w-[380px] lg:flex"
      />
      {panelOpen && (
        <SidePanel
          onUnauthorized={onUnauthorized}
          roomName={roomName}
          className="fixed inset-x-0 top-14 bottom-0 z-40 flex lg:hidden"
        />
      )}
      <StartAudioButton label="Start Audio" />
      <Toaster
        icons={{
          warning: <WarningIcon weight="bold" />,
        }}
        position="top-center"
        className="toaster group"
        style={
          {
            '--normal-bg': 'var(--popover)',
            '--normal-text': 'var(--popover-foreground)',
            '--normal-border': 'var(--border)',
          } as React.CSSProperties
        }
      />
    </AgentSessionProvider>
  );
}
