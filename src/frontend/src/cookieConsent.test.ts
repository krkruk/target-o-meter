// Phase 3: cookie consent wrapper configuration contract.
//
// vanilla-cookieconsent manipulates document.body and reads localStorage, so
// asserting on rendered banner DOM in jsdom is brittle (covered by manual
// verification). Instead this test pins the *configuration invariants* a
// future edit could silently break: the policy link points at /privacy, the
// necessary-only category invariant holds, and the wrapper exposes the
// init→cleanup lifecycle shape main.tsx depends on.
import { describe, it, expect } from 'vitest';
import { initCookieConsent, consentConfig, englishTranslation, PRIVACY_POLICY_URL } from './cookieConsent';

describe('cookieConsent wrapper', () => {
  it('exposes the privacy policy URL pointing at the /privacy route', () => {
    expect(PRIVACY_POLICY_URL).toBe('/privacy');
  });

  it('configures exactly one user-facing category: necessary (readOnly)', () => {
    // The site uses strictly-necessary cookies only. A future edit that adds
    // an analytics/marketing category without bumping the revision would
    // break the "necessary only" invariant the privacy page documents.
    const categories = Object.keys(consentConfig.categories);
    expect(categories).toEqual(['necessary']);
    expect(consentConfig.categories.necessary.enabled).toBe(true);
    expect(consentConfig.categories.necessary.readOnly).toBe(true);
  });

  it('embeds the /privacy link in the consent modal description', () => {
    // The banner's "Cookie Policy" link must resolve to the /privacy route
    // (Phase 2). The description HTML is where vanilla-cookieconsent v3
    // carries the policy link. `englishTranslation` is the narrow typed
    // export (the config's translations[locale] is a union type).
    expect(englishTranslation.consentModal.description).toContain('/privacy');
  });

  it('exposes an init function that returns a cleanup function', () => {
    // main.tsx calls initCookieConsent() in a useEffect and wires the return
    // into the effect's teardown. The return MUST be a function (cleanup).
    const cleanup = initCookieConsent();
    expect(typeof cleanup).toBe('function');
    cleanup();
  });
});
