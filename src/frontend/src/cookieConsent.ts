// Phase 3: cookie consent banner wrapper.
//
// Encapsulates vanilla-cookieconsent v3's configuration + lifecycle so
// main.tsx stays a thin entry point and the consent config is auditable in
// one place. The banner is mounted site-wide from main.tsx (around <App />);
// the library owns its own DOM portal (appended to document.body).
//
// CONFIGURATION INVARIANTS (pinned by cookieConsent.test.ts):
//   - privacyPolicyUrl is "/privacy" (the Phase 2 route).
//   - exactly one user-facing category: "necessary" (readOnly, enabled).
//     No analytics/marketing category is registered — the site uses
//     strictly-necessary cookies only. When a tracking service lands, add
//     its category here AND bump `revision` so stored consent re-prompts.
import * as CookieConsent from 'vanilla-cookieconsent';
import 'vanilla-cookieconsent/dist/cookieconsent.css';
import type { CookieConsentConfig, Translation } from 'vanilla-cookieconsent';

/** URL of the cookie policy page (Phase 2 route). */
export const PRIVACY_POLICY_URL = '/privacy';

/**
 * English translation, defined separately so its shape is the narrow
 * `Translation` type (the config's `translations[locale]` field is a union
 * of string | Translation | (() => ...) which would otherwise need a cast
 * to read in tests). Reused by the config below.
 */
const enTranslation: Translation = {
  consentModal: {
    title: 'We use cookies',
    description:
      'We use strictly-necessary cookies to keep you signed in and to ' +
      'protect forms. No analytics or marketing cookies are used. See ' +
      `our <a href="${PRIVACY_POLICY_URL}">Cookie Policy</a>.`,
    acceptAllBtn: 'Accept all',
    acceptNecessaryBtn: 'Reject all',
    showPreferencesBtn: 'Manage preferences',
  },
  preferencesModal: {
    title: 'Manage cookie preferences',
    acceptAllBtn: 'Accept all',
    acceptNecessaryBtn: 'Reject all',
    savePreferencesBtn: 'Save preferences',
    closeIconLabel: 'Close',
    sections: [
      {
        title: 'Strictly-necessary cookies',
        description:
          'Required for login and security. These cannot be disabled. ' +
          `See our <a href="${PRIVACY_POLICY_URL}">Cookie Policy</a>.`,
        linkedCategory: 'necessary',
      },
    ],
  },
};

/**
 * The consent configuration, exported so the test suite can pin the
 * invariants (necessary-only category, /privacy policy link, revision).
 */
export const consentConfig: CookieConsentConfig = {
  revision: 1,
  // Necessary category covers sessionid + csrftoken. readOnly + enabled
  // means the user cannot disable it (it is strictly necessary).
  categories: {
    necessary: {
      enabled: true,
      readOnly: true,
    },
  },
  guiOptions: {
    consentModal: {
      // Accept and Reject styled with equal weight — GDPR-compliant, no
      // dark pattern. (equalWeightButtons defaults to true; set explicitly
      // so a future library default change can't silently undo it.)
      equalWeightButtons: true,
      layout: 'box inline',
      position: 'bottom',
    },
  },
  language: {
    default: 'en',
    translations: {
      en: enTranslation,
    },
  },
};

/** Exposed for tests so they can assert on the description without a cast. */
export const englishTranslation = enTranslation;

/**
 * Initialize the cookie consent banner. Call once (e.g. in a useEffect from
 * main.tsx). Returns a cleanup function that destroys the banner — wire it
 * into the effect's teardown so React Strict Mode's double-invoke in dev
 * doesn't leave a stale portal.
 */
export function initCookieConsent(): () => void {
  let destroyed = false;
  // CookieConsent.run is async; fire-and-forget — the banner appears once
  // configured. Errors are swallowed because a failed consent init must not
  // crash the SPA (consent is non-blocking for strictly-necessary cookies).
  CookieConsent.run(consentConfig).catch(() => {
    /* banner init failed — non-fatal */
  });

  return () => {
    if (destroyed) return;
    destroyed = true;
    try {
      // v3.1.0 exposes reset() (not destroy()). eraseCookie defaults to
      // false; we keep stored consent across React Strict Mode remounts.
      CookieConsent.reset();
    } catch {
      /* already reset — non-fatal */
    }
  };
}
