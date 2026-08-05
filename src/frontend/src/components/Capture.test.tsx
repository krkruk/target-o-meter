// fix/add-missing-warning-and-gallery-button-in-mobile: /capture contract.
// /capture is no longer the mobile entry point (Dashboard routes every
// platform to /upload), but it stays as a direct-URL fallback. The PII
// warning must render there too — a safety net so any user who lands on
// /capture directly still sees the warning regardless of platform.
//
// The expected text is a literal in the test (NOT imported from the module)
// so this is a real oracle against wording drift, mirroring Upload.test.tsx.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { Capture } from './Capture';

vi.mock('../api', () => ({
  createScoringJob: vi.fn(async () => ({ job_id: 'job-1' })),
}));

// The warning renders verbatim (user decision: "keep my original"). Kept as an
// independent literal here — if the shared constant drifts, this fails.
const WARNING_TEXT =
  'The data is used to train LLM models. Do not upload Personal Identifiable Information. ' +
  'By uploading the image, you agree to effectively make this information public. ' +
  'Think about it and proceed responsibly.';

async function renderAtCaptureStep() {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/capture']}>
      <Capture />
    </MemoryRouter>,
  );
  // Advance past the CaliberDistanceStep wizard so the capture UI mounts.
  await user.click(screen.getByRole('button', { name: /next/i }));
}

describe('Capture (capture step)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the verbatim PII/LLM-training warning in a role="note" callout', async () => {
    await renderAtCaptureStep();
    const note = screen.getByRole('note');
    expect(note).toBeInTheDocument();
    expect(note.textContent?.replace(/\s+/g, ' ').trim()).toBe(WARNING_TEXT);
  });
});
