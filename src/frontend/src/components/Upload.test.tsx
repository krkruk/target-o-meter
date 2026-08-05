// ui-chores Phase 5: /upload PII warning + camera button contract.
//   (a) the verbatim PII/LLM-training warning renders,
//   (b) both "Choose file" and "Take a picture" buttons are present,
//   (c) clicking each button calls its hidden input ref's .click(),
//   (d) the camera input carries capture="environment" and the file input does not.
// The mobile-only visibility is media-query-driven and not assertable in jsdom
// (per plan); it's covered by the Playwright acceptance test instead.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { Upload } from './Upload';

vi.mock('../api', () => ({
  createScoringJob: vi.fn(async () => ({ job_id: 'job-1' })),
}));

// The warning renders verbatim (user decision: "keep my original").
const WARNING_TEXT =
  'The data is used to train LLM models. Do not upload Personal Identifiable Information. ' +
  'By uploading the image, you agree to effectively make this information public. ' +
  'Think about it and proceed responsibly.';

async function renderAtUploadStep() {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/upload']}>
      <Upload />
    </MemoryRouter>,
  );
  // Advance past the CaliberDistanceStep wizard so the upload UI mounts.
  await user.click(screen.getByRole('button', { name: /next/i }));
}

describe('Upload (upload step)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the verbatim PII/LLM-training warning in a role="note" callout', async () => {
    await renderAtUploadStep();
    const note = screen.getByRole('note');
    expect(note).toBeInTheDocument();
    expect(note.textContent?.replace(/\s+/g, ' ').trim()).toBe(WARNING_TEXT);
  });

  it('renders both "Choose file" and "Take a picture" buttons', async () => {
    await renderAtUploadStep();
    expect(screen.getByRole('button', { name: /choose file/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /take a picture/i })).toBeInTheDocument();
  });

  it('puts capture="environment" on the camera input only', async () => {
    await renderAtUploadStep();
    // The camera input is the one inside the .mobileOnly wrapper next to the button.
    const allInputs = document.querySelectorAll('input[type="file"]');
    expect(allInputs.length).toBe(2);
    const [fileInput, cameraInput] = Array.from(allInputs);
    expect(cameraInput.hasAttribute('capture')).toBe(true);
    expect(cameraInput.getAttribute('capture')).toBe('environment');
    expect(fileInput.hasAttribute('capture')).toBe(false);
  });

  it('clicking "Choose file" triggers the file input ref .click()', async () => {
    const user = userEvent.setup();
    await renderAtUploadStep();
    const fileInput = document.querySelector('input[type="file"]:not([capture])') as HTMLInputElement;
    const spy = vi.spyOn(fileInput, 'click');
    await user.click(screen.getByRole('button', { name: /choose file/i }));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('clicking "Take a picture" triggers the camera input ref .click()', async () => {
    const user = userEvent.setup();
    await renderAtUploadStep();
    const cameraInput = document.querySelector('input[type="file"][capture]') as HTMLInputElement;
    const spy = vi.spyOn(cameraInput, 'click');
    await user.click(screen.getByRole('button', { name: /take a picture/i }));
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
