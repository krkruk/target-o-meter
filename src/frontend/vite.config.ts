import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Vite config for the Target-o-meter SPA (S-01).
//
// django-vite 3.1.0 serves the dev server (HMR) when Django runs in DEBUG,
// and reads the built manifest from `build.outDir` (`dist/`) when in prod.
// `build.manifest: true` is required by django-vite's prod mode; without it
// Vite emits no manifest.json and django-vite cannot resolve hashed assets.
//
// The build entry is the source file `src/main.tsx` (declared explicitly via
// `rollupOptions.input`), NOT Vite's default `index.html`. This is what makes
// django-vite's `{% vite_asset 'src/main.tsx' %}` resolve: the manifest is
// keyed by the source path, so the template tag finds the hashed bundle. The
// standalone `index.html` is kept only for running the Vite dev server on its
// own (fast component iteration without Django) — the real app serves
// `templates/base.html` through Django.
//
// The Vitest config is co-located here (rather than a separate vitest.config)
// so `npm test` and `npm run dev` share one entry point.
export default defineConfig({
  plugins: [react()],
  // Base path. django-vite prepends Django's STATIC_URL ('static/') to the
  // dev-server URLs it emits, so the browser fetches e.g.
  // http://localhost:5173/static/src/main.tsx. Vite must serve under that
  // same base or it returns its HTML SPA-fallback (with text/html) for the
  // prefixed path — the browser receives HTML where it expected a JS module,
  // the import fails, and #root stays empty (the blank-page bug).
  // In prod the built assets are served from /static/ by Django/WhiteNoise,
  // so the same base applies.
  base: '/static/',
  build: {
    outDir: 'dist',
    manifest: 'manifest.json',
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, 'src/main.tsx'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    // CSS Modules: return class names as-is so tests can assert on them and
    // components don't crash trying to parse real CSS in jsdom.
    css: { modules: { classNameStrategy: 'non-scoped' } },
    // Exclude the Playwright acceptance tests (tests-acceptance/*.spec.ts) —
    // they drive a real browser + live stack, not jsdom. They run via
    // `npm run test:acceptance` (npx playwright test).
    exclude: ['node_modules', 'dist', 'tests-acceptance/**'],
  },
});
