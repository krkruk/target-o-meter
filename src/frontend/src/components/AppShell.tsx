// AppShell — the authenticated layout.
//
// TopBar (brand + nick) + collapsible Sidebar (Home top, Logout bottom) + a
// routed main area. Owns the collapsed state so the toggle stays local; App
// passes me + onLogout. S-02 replaced the S-01 placeholder main with <Routes>
// — the five wizard routes (Dashboard / Capture / Upload / Waiting / Results)
// own the main content. Sidebar's Home navigates to /dashboard via the router.
//
// Phases 7-8 swap each stub route component for the real implementation; the
// route table here is the wiring point.
import { useState } from 'react';
import { Routes, Route } from 'react-router-dom';
import type { Me } from '../api';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';
import { Dashboard } from './Dashboard';
import { Capture } from './Capture';
import { Upload } from './Upload';
import { Waiting } from './Waiting';
import { Results } from './Results';
import { ScoreDashboard } from './ScoreDashboard';
import { AdminUsersPage } from './AdminUsersPage';
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
      <TopBar nick={me.user?.nick ?? ''} onLogout={onLogout} />
      <div className={styles.body}>
        <Sidebar
          collapsed={collapsed}
          onToggle={() => setCollapsed((c) => !c)}
          onLogout={onLogout}
          isOwner={isOwner}
        />
        <main className={styles.main} role="main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/scores" element={<ScoreDashboard />} />
            <Route path="/capture" element={<Capture />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/waiting/:jobId" element={<Waiting />} />
            <Route path="/results/:jobId" element={<Results />} />
            {/* S-04: owner-only user management (Admin link in Sidebar). RBAC is
                enforced server-side (require_owner); a non-owner visiting /admin
                sees the page but getAdminUsers 403s → "Owner privileges required". */}
            <Route path="/admin" element={<AdminUsersPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
