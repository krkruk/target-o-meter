// Phase 2: trivial render to prove the Django↔Vite handoff works before
// building the real Welcome / AppShell screens (those land in Phase 3).
// Replaced wholesale in Phase 3 — do NOT add behaviour here.
import { createRoot } from 'react-dom/client';

const rootEl = document.getElementById('root');
if (rootEl) {
  createRoot(rootEl).render(<h1>Hello from React</h1>);
}
