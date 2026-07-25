// Phase 3: AppShell — the authenticated layout.
//
// TopBar (brand + nick) + collapsible Sidebar (Home top, Logout bottom) +
// a dashboard placeholder main area. Owns the collapsed state so the toggle
// stays local; App passes me + onLogout. S-02/S-03 replace the placeholder.
import { useState } from 'react';
import type { Me } from '../api';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';
import styles from './AppShell.module.css';

interface AppShellProps {
  me: Me;
  onLogout: () => void;
}

export function AppShell({ me, onLogout }: AppShellProps) {
  const [collapsed, setCollapsed] = useState(false);
  const isOwner = me.user?.role === 'owner';

  return (
    <div className={styles.shell} data-shell data-collapsed={collapsed}>
      <TopBar nick={me.user?.nick ?? ''} />
      <div className={styles.body}>
        <Sidebar
          collapsed={collapsed}
          onToggle={() => setCollapsed((c) => !c)}
          onLogout={onLogout}
          isOwner={isOwner}
        />
        <main className={styles.main} role="main">
          <div className={styles.placeholder}>
            <h2>Your dashboard will appear here</h2>
            <p>Scoring and results land in a later update.</p>
          </div>
        </main>
      </div>
    </div>
  );
}
