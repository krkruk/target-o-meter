// App — the auth-seam decision point.
//
// Single useState: fetch /v1/me on mount, then render Welcome (unauthed) or
// AppShell (authed). When the authed user hasn't chosen a nick yet
// (has_set_nick === false), the NickPrompt overlays the shell.
//
// S-02: React Router landed. The authed branch mounts <BrowserRouter> so the
// five wizard routes (/dashboard, /capture, /upload, /waiting/:jobId,
// /results/:jobId) own the AppShell main area. The auth seam (one GET) is
// unchanged — only the authed branch gained the router.
import { useEffect, useState } from 'react';
import { BrowserRouter } from 'react-router-dom';
import { getMe, login, postLogout, type Me } from './api';
import { Welcome } from './components/Welcome';
import { AppShell } from './components/AppShell';
import { NickPrompt } from './components/NickPrompt';
import { CookieSettingsButton } from './components/CookieSettingsButton';

const UNAUTHENTICATED: Me = { authenticated: false, user: null };

export function App() {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((result) => { if (!cancelled) setMe(result); })
      .catch(() => { if (!cancelled) setMe(UNAUTHENTICATED); });
    return () => { cancelled = true; };
  }, []);

  if (me === null) {
    return (
      <div role="status" aria-label="Loading">
        Loading…
      </div>
    );
  }

  if (!me.authenticated) {
    return (
      <>
        <Welcome onLogin={login} />
        <CookieSettingsButton />
      </>
    );
  }

  const showNickPrompt = me.user ? !me.user.has_set_nick : false;

  return (
    <BrowserRouter>
      <AppShell me={me} onLogout={handleLogout} />
      {showNickPrompt && <NickPrompt onNickSet={setMe} />}
      <CookieSettingsButton />
    </BrowserRouter>
  );

  async function handleLogout() {
    try {
      await postLogout();
    } finally {
      // Reload to the welcome page regardless — the Django session is cleared
      // server-side; even if the Auth0 /v2/logout redirect errors without
      // creds, the local session is gone.
      window.location.reload();
    }
  }
}
