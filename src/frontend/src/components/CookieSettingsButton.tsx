// Phase 3: persistent "Cookie settings" control.
//
// vanilla-cookieconsent v3 re-opens its modal when ANY element with the
// data-cc="show-preferencesModal" attribute is clicked — the library
// registers a global click listener, so this button needs no JS glue. It is
// rendered site-wide (in App, as a sibling of Welcome/AppShell) so a visitor
// can revise consent after dismissing the banner.
//
// Positioned fixed at the bottom-left so it stays reachable without
// competing with the header's Star/Login actions.
export function CookieSettingsButton() {
  return (
    <button
      type="button"
      data-cc="show-preferencesModal"
      style={{
        position: 'fixed',
        bottom: '1rem',
        left: '1rem',
        zIndex: 9000,
        padding: '0.35rem 0.75rem',
        fontSize: '0.85rem',
        border: '1px solid var(--color-border, #e3e1dc)',
        background: 'var(--color-bg, #f7f7f5)',
        color: 'var(--color-muted, #6a6a6a)',
        borderRadius: '6px',
        cursor: 'pointer',
      }}
    >
      Cookie settings
    </button>
  );
}
