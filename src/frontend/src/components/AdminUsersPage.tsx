// S-04 Phase 3-4: AdminUsersPage — the owner's user-management page.
//
// Searchable, paginated list of registered users with ban-status chips.
// Phase 3 ships the read-only list (search, pagination, chips, 403 handling);
// Phase 4 enables the Ban / Unban / Delete actions.
//
// The owner audience is different from /v1/me: rows carry `sub` (the owner
// needs it to match rows against the Auth0 dashboard). Ban status drives the
// chip color: red "Active ban" / grey "Banned before" / nothing.
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getAdminUsers,
  unbanUser,
  type AdminUser,
  type AdminUserList,
  type BanStatus,
} from '../api';
import { BanModal } from './BanModal';
import { DeleteUserModal } from './DeleteUserModal';
import styles from './AdminUsersPage.module.css';

const DEBOUNCE_MS = 250;

type Modal = 'ban' | 'delete';

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

  // Phase 4: update a row in-place after a successful ban/unban, or remove it
  // after a delete. Mutating the existing items array (not refetching) keeps
  // the pager + scroll position stable for the owner.
  const handleBanStatusChanged = useCallback((sub: string, status: BanStatus) => {
    setData((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        items: prev.items.map((u) => (u.sub === sub ? { ...u, ban: status } : u)),
      };
    });
  }, []);

  const handleDeleted = useCallback((sub: string) => {
    setData((prev) => {
      if (!prev) return prev;
      const items = prev.items.filter((u) => u.sub !== sub);
      // Derive total_pages from the decremented GLOBAL total, not the
      // page-local items.length — otherwise deleting the last row on page 1
      // of a 2-page list would shrink items.length below page_size and hide
      // the pager, making page 2 unreachable until a refetch.
      const total = Math.max(0, prev.total - 1);
      return {
        ...prev,
        items,
        total,
        total_pages: Math.max(1, Math.ceil(total / prev.page_size)),
      };
    });
  }, []);

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
            <UserRow
              key={u.user_uuid}
              user={u}
              onBanStatusChanged={handleBanStatusChanged}
              onDeleted={handleDeleted}
            />
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

function UserRow({
  user,
  onBanStatusChanged,
  onDeleted,
}: {
  user: AdminUser;
  onBanStatusChanged: (sub: string, status: BanStatus) => void;
  onDeleted: (sub: string) => void;
}) {
  const [modal, setModal] = useState<Modal | null>(null);
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);

  async function handleUnban() {
    setBusy(true);
    setRowError(null);
    try {
      const status = await unbanUser(user.sub);
      onBanStatusChanged(user.sub, status);
    } catch (err) {
      const status = (err as { status?: number })?.status;
      setRowError(status === 409 ? 'No active ban.' : 'Could not unban. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={styles.row} data-row data-owner={user.is_owner}>
      <div className={styles.identity}>
        <span className={styles.nick}>{user.nick}</span>
        <span className={styles.sub}>{user.sub}</span>
      </div>
      <BanChip ban={user.ban} />
      {!user.is_owner && (
        <div className={styles.actions}>
          {user.ban.is_banned ? (
            <button
              type="button"
              className={styles.actionBtn}
              onClick={handleUnban}
              disabled={busy}
            >
              {busy ? '…' : 'Unban'}
            </button>
          ) : (
            <button
              type="button"
              className={styles.actionBtn}
              onClick={() => setModal('ban')}
            >
              Ban
            </button>
          )}
          <button
            type="button"
            className={`${styles.actionBtn} ${styles.dangerBtn}`}
            onClick={() => setModal('delete')}
          >
            Delete
          </button>
        </div>
      )}
      {rowError && <span className={styles.rowError} role="alert">{rowError}</span>}
      {modal === 'ban' && (
        <BanModal
          user={user}
          onClose={() => setModal(null)}
          onBanned={(status) => {
            onBanStatusChanged(user.sub, status);
            setModal(null);
          }}
        />
      )}
      {modal === 'delete' && (
        <DeleteUserModal
          user={user}
          onClose={() => setModal(null)}
          onDeleted={() => {
            onDeleted(user.sub);
            setModal(null);
          }}
        />
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
