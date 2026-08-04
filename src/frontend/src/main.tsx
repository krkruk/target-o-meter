// SPA entry point. Phase 3 replaced the Phase 2 trivial render with the real
// App (auth seam + Welcome/AppShell/NickPrompt). django-vite resolves this
// module via the {% vite_asset 'src/main.tsx' %} tag in templates/base.html.
//
// Phase 3 (cookie consent): the consent banner mounts site-wide here, via a
// useEffect, so it runs exactly once for the whole SPA and renders above
// whichever branch the auth seam takes (Welcome or AppShell). The library
// owns its own DOM portal (appended to document.body); we only own init +
// destroy. Consent state is NOT passed into <App /> — the library is
// self-contained.
import { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { initCookieConsent } from './cookieConsent';
import './styles.css';

const rootEl = document.getElementById('root');
if (rootEl) {
  createRoot(rootEl).render(
    <>
      <App />
      <CookieConsentMount />
    </>,
  );
}

/**
 * Mounts the vanilla-cookieconsent banner once, site-wide. Rendered as a
 * sibling of <App /> (renders nothing itself — the banner's DOM is owned by
 * the library). The cleanup calls CookieConsent.destroy() so React Strict
 * Mode's double-invoke in dev doesn't leave a stale portal.
 */
function CookieConsentMount() {
  useEffect(() => initCookieConsent(), []);
  return null;
}
