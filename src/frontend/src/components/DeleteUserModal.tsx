// S-04 Phase 4: DeleteUserModal — hard-delete confirmation.
//
// Hard-delete is irreversible, so the Auth0 reminder note is mandatory:
// Target-o-meter cannot delete the Auth0 user, the owner must do that in the
// Auth0 dashboard. Submit → deleteUser; 409 shows "Cannot delete the owner".
// Dismissable (Esc / overlay click / Cancel).
import { useEffect, useState } from 'react';
import { deleteUser, type AdminUser } from '../api';
import styles from './DeleteUserModal.module.css';

interface DeleteUserModalProps {
  user: AdminUser;
  onClose: () => void;
  onDeleted: () => void;
}

export function DeleteUserModal({ user, onClose, onDeleted }: DeleteUserModalProps) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Esc-to-dismiss (overlay-click + Cancel are wired below). A pending submit
  // is left alone so Esc can't interrupt an in-flight delete.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !pending) onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, pending]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      await deleteUser(user.sub);
      onDeleted();
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) setError('Cannot delete the owner.');
      else if (status === 404) setError('User not found.');
      else setError('Could not delete the user. Try again.');
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      className={styles.overlay}
      data-overlay
      role="dialog"
      aria-label={`Delete ${user.nick}`}
      onClick={onClose}
    >
      <form
        className={styles.card}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <h2 className={styles.title}>Delete {user.nick}?</h2>
        <p className={styles.warning}>
          This permanently removes the user from Target-o-meter. Their ban history is
          deleted too. This cannot be undone.
        </p>
        <p className={styles.reminder}>
          Also delete this person in Auth0 → Users tab; Target-o-meter cannot do that
          for you.
        </p>

        {error && <p className={styles.error} role="alert">{error}</p>}

        <div className={styles.actions}>
          <button type="button" className={styles.cancel} onClick={onClose} disabled={pending}>
            Cancel
          </button>
          <button type="submit" className={styles.danger} disabled={pending}>
            {pending ? 'Deleting…' : 'Delete permanently'}
          </button>
        </div>
      </form>
    </div>
  );
}
