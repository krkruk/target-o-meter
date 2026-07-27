// Playwright globalTeardown: tear down the stack the globalSetup booted.
//
// Kills the whole process group for each subprocess (start_new_session/
// detached means the children form a group) and removes the run dir on
// success. On failure the run dir is left under results/playwright-<pid>/ for
// post-mortem (the captured runserver + qcluster + vite logs land there via
// the stdio pipes in the setup).
import { rmSync } from 'node:fs';
import type { StackHandle } from './global-setup';

export default async function globalTeardown(): Promise<void> {
  const handle = (globalThis as unknown as { __stack?: StackHandle }).__stack;
  if (!handle) return;
  for (const proc of handle.processes) {
    try {
      // detached:true created a new session; killing the group reaches
      // runserver/qcluster/vite + any children they spawned.
      if (proc.pid) process.kill(-proc.pid, 'SIGTERM');
    } catch {
      // Process may already be dead; ignore.
    }
  }
  // Give them a moment to exit, then SIGKILL any survivors.
  await new Promise((r) => setTimeout(r, 500));
  for (const proc of handle.processes) {
    try {
      if (proc.pid) process.kill(-proc.pid, 'SIGKILL');
    } catch {
      // already gone
    }
  }
  rmSync(handle.runDir, { recursive: true, force: true });
}
