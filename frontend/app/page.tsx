import { App } from '@/components/app/app';
import { LoginView } from '@/components/app/login-view';
import { hasSession } from '@/lib/server/auth';

// Rendered per request: the session cookie decides between the passcode screen and the app (SPEC §12).
export const dynamic = 'force-dynamic';

export default async function Page() {
  if (!(await hasSession())) {
    return (
      <main className="grid min-h-svh place-content-center py-16">
        <LoginView />
      </main>
    );
  }
  return <App />;
}
