// S-04 Phase 4: BanModal — the ban UX.
//
// Duration dropdown (1h / 1 day / 7 days / 30 days) + required free-text
// reason (min 5 chars). Reuses the NickPrompt overlay/card pattern, but is
// DISMISSABLE (Esc / overlay click / Cancel) — unlike NickPrompt which is
// mandatory on first login. On submit → banUser; 409 (cannot ban the owner)
// surfaces inline.
import { useEffect, useState } from 'react';
import { banUser, type AdminUser, type BanDuration, type BanStatus } from '../api';
import styles from './BanModal.module.css';

interface BanModalProps {
  user: AdminUser;
  onClose: () => void;
  onBanned: (status: BanStatus) => void;
}

const REASON_MIN = 5;
const REASON_MAX = 500;

export function BanModal({ user, onClose, onBanned }: BanModalProps) {
  const [duration, setDuration] = useState<BanDuration>('1d');
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Esc-to-dismiss (overlay-click + Cancel are wired below). A pending submit
  // is left alone so Esc can't interrupt an in-flight ban.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !pending) onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, pending]);

  const trimmed = reason.trim();
  const canSubmit = trimmed.length >= REASON_MIN && !pending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setPending(true);
    setError(null);
    try {
      const status = await banUser(user.sub, { duration, reason: trimmed });
      onBanned(status);
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) setError('Cannot ban the owner.');
      else if (status === 404) setError('User not found.');
      else if (status === 422) setError('Invalid duration or reason too short.');
      else setError('Could not ban the user. Try again.');
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      className={styles.overlay}
      data-overlay
      role="dialog"
      aria-label={`Ban ${user.nick}`}
      onClick={onClose}
    >
      <form
        className={styles.card}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <h2 className={styles.title}>Ban {user.nick}</h2>
        <p className={styles.hint}>
          The user is blocked at their next login until the ban expires or is lifted.
        </p>

        <label className={styles.field}>
          <span className={styles.label}>Duration</span>
          <select
            className={styles.select}
            aria-label="Duration"
            value={duration}
            onChange={(e) => setDuration(e.target.value as BanDuration)}
          >
            <option value="1h">1 hour</option>
            <option value="1d">1 day</option>
            <option value="7d">7 days</option>
            <option value="30d">30 days</option>
          </select>
        </label>

        <label className={styles.field}>
          <span className={styles.label}>Reason (required, {REASON_MIN}–{REASON_MAX} chars)</span>
          <textarea
            className={styles.textarea}
            aria-label="Reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={REASON_MAX}
            rows={3}
            autoFocus
          />
        </label>

        {error && <p className={styles.error} role="alert">{error}</p>}

        <div className={styles.actions}>
          <button type="button" className={styles.cancel} onClick={onClose} disabled={pending}>
            Cancel
          </button>
          <button type="submit" className={styles.submit} disabled={!canSubmit}>
            {pending ? 'Banning…' : 'Confirm ban'}
          </button>
        </div>
      </form>
    </div>
  );
}
