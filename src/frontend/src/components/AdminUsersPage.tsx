// S-04 Phase 3-4: AdminUsersPage — the owner's user-management page.
//
// Searchable, paginated list of registered users with ban-status chips.
// Phase 3 ships the read-only list (search, pagination, chips, 403 handling);
// Phase 4 enables the Ban / Unban / Delete actions (the buttons render now but
// are wired in Phase 4).
//
// The owner audience is different from /v1/me: rows carry `sub` (the owner
// needs it to match rows against the Auth0 dashboard). Ban status drives the
// chip color: red "Active ban" / grey "Banned before" / nothing.
import { useCallback, useEffect, useRef, useState } from 'react';
import { getAdminUsers, type AdminUser, type AdminUserList } from '../api';
import styles from './AdminUsersPage.module.css';

const DEBOUNCE_MS = 250;

export function AdminUsersPage() {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AdminUserList | null>(null);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);

  // Debounced fetch: re-runs when the debounced query or the page changes.
  const debouncedRef = useRef<number | undefined>(undefined);
  const [debouncedQuery, setDebouncedQuery] = useState('');

  useEffect(() => {
    window.clearTimeout(debouncedRef.current);
    debouncedRef.current = window.setTimeout(() => {
      setDebouncedQuery(query);
      setPage(1);
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(debouncedRef.current);
  }, [query]);

  const load = useCallback(async () => {
    setPending(true);
    setError(null);
    try {
      const result = await getAdminUsers({ q: debouncedQuery || undefined, page });
      setData(result);
    } catch (e) {
      const status = (e as { status?: number })?.status ?? 0;
      setError({
        status,
        message: status === 403
          ? 'Owner privileges required'
          : status === 401
            ? 'Your session has expired — please log in again.'
            : 'Failed to load users.',
      });
      setData(null);
    } finally {
      setPending(false);
    }
  }, [debouncedQuery, page]);

  useEffect(() => { void load(); }, [load]);

  if (pending && !data) {
    return <div role="status" aria-label="Loading users">Loading users…</div>;
  }
  if (error) {
    return <div role="alert">{error.message}</div>;
  }
  if (!data) return null;

  return (
    <section className={styles.page} aria-label="User administration">
      <header className={styles.header}>
        <h1>Users</h1>
        <input
          type="search"
          className={styles.search}
          aria-label="Search users"
          placeholder="Search by nick or sub…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </header>

      {data.items.length === 0 ? (
        <p className={styles.empty}>No users match.</p>
      ) : (
        <ul className={styles.list} role="list">
          {data.items.map((u) => (
            <UserRow key={u.user_uuid} user={u} />
          ))}
        </ul>
      )}

      {data.total_pages > 1 && (
        <nav className={styles.pager} aria-label="Pagination">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <span>Page {data.page} of {data.total_pages}</span>
          <button
            type="button"
            disabled={page >= data.total_pages}
            onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}
          >
            Next
          </button>
        </nav>
      )}
    </section>
  );
}

function UserRow({ user }: { user: AdminUser }) {
  // Phase 4 wires the mutations; Phase 3 renders disabled affordances so the
  // page shape is stable. The owner row never gets ban/delete buttons (the
  // server guards too, but the UI must not offer the action).
  return (
    <li className={styles.row} data-row data-owner={user.is_owner}>
      <div className={styles.identity}>
        <span className={styles.nick}>{user.nick}</span>
        <span className={styles.sub}>{user.sub}</span>
      </div>
      <BanChip ban={user.ban} />
      {!user.is_owner && (
        <div className={styles.actions}>
          <BanButton user={user} />
          <DeleteButton user={user} />
        </div>
      )}
    </li>
  );
}

function BanChip({ ban }: { ban: AdminUser['ban'] }) {
  if (ban.is_banned) {
    return <span className={`${styles.chip} ${styles.activeBan}`}>Active ban</span>;
  }
  if (ban.has_prior_ban) {
    return <span className={`${styles.chip} ${styles.priorBan}`}>Banned before</span>;
  }
  return null;
}

// Phase 4 wires these. Kept as inert elements in Phase 3 so the page layout
// (and the "owner row has no buttons" test) is stable.
function BanButton({ user }: { user: AdminUser }) {
  const label = user.ban.is_banned ? 'Unban' : 'Ban';
  return <button type="button" className={styles.actionBtn} disabled>{label}</button>;
}

function DeleteButton({ user: _user }: { user: AdminUser }) {
  return <button type="button" className={`${styles.actionBtn} ${styles.dangerBtn}`} disabled>Delete</button>;
}
