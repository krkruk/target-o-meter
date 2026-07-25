// Phase 3: NickPrompt — the first-login nick UX (FR-002).
//
// Rendered over the AppShell when me.user.has_set_nick is false. The nick is
// mandatory on first login, so the prompt has no dismiss control: the submit
// is disabled until a non-empty (after trim) nick is entered, and on success
// the updated Me is forwarded via onNickSet (App swaps the prompt out).
//
// Error handling: a 409 (nick taken) surfaces an inline message and the
// prompt stays open so the user can pick another. Any other failure also
// surfaces an inline message rather than silently closing.
import { useState } from 'react';
import { patchMe, type Me } from '../api';
import styles from './NickPrompt.module.css';

interface NickPromptProps {
  onNickSet: (me: Me) => void;
}

export function NickPrompt({ onNickSet }: NickPromptProps) {
  const [nick, setNick] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = nick.trim();
  const canSubmit = trimmed.length > 0 && !pending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setPending(true);
    setError(null);
    try {
      const updated = await patchMe(trimmed);
      onNickSet(updated);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // The api client throws "PATCH /v1/me failed: <status>"; a 409 means
      // the CI-unique constraint caught a duplicate.
      setError(msg.includes('409') ? 'That nick is already taken.' : 'Could not save nick. Try again.');
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={styles.overlay} role="dialog" aria-label="Choose a username">
      <form className={styles.card} onSubmit={handleSubmit}>
        <h2 className={styles.title}>Choose a username</h2>
        <p className={styles.hint}>This is how you'll appear on the range. You can change it later.</p>
        <label className={styles.field}>
          <span className={styles.label}>Nick</span>
          <input
            className={styles.input}
            type="text"
            aria-label="Nick"
            value={nick}
            onChange={(e) => setNick(e.target.value)}
            maxLength={64}
            autoComplete="username"
            autoFocus
          />
        </label>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <button className={styles.submit} type="submit" disabled={!canSubmit}>
          {pending ? 'Saving…' : 'Save'}
        </button>
      </form>
    </div>
  );
}
