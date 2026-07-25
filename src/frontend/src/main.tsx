// SPA entry point. Phase 3 replaced the Phase 2 trivial render with the real
// App (auth seam + Welcome/AppShell/NickPrompt). django-vite resolves this
// module via the {% vite_asset 'src/main.tsx' %} tag in templates/base.html.
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles.css';

const rootEl = document.getElementById('root');
if (rootEl) {
  createRoot(rootEl).render(<App />);
}
