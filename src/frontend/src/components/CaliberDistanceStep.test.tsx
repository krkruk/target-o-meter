// S-02 Phase 8: CaliberDistanceStep + Capture/Upload wizard contracts.
//
// CaliberDistanceStep: two accessible <select>s (caliber + distance). "Next"
// advances to the media-acquisition step (the parent renders either the camera
// input or the file picker).
//
// Capture/Upload: render CaliberDistanceStep first, then the media input. On
// file selection, call createScoringJob and navigate to /waiting/:jobId.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import * as api from '../api';
import { CaliberDistanceStep } from './CaliberDistanceStep';
import { Capture } from './Capture';
import { Upload } from './Upload';

describe('CaliberDistanceStep', () => {
  it('renders accessible caliber + distance + weapon_type + target_type selects with labels', () => {
    render(
      <CaliberDistanceStep onNext={() => {}} />
    );
    expect(screen.getByRole('combobox', { name: /caliber/i })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /distance/i })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /weapon type/i })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /target type/i })).toBeInTheDocument();
  });

  it('calls onNext with all four selections when Next is activated', async () => {
    const onNext = vi.fn();
    render(<CaliberDistanceStep onNext={onNext} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /caliber/i }), '9x19mm');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /distance/i }), '25');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /weapon type/i }), 'sport_pistol');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /target type/i }), 'precision_pistol');
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(onNext).toHaveBeenCalledWith({
      caliber: '9x19mm', distance_m: 25,
      weapon_type: 'sport_pistol', target_type: 'precision_pistol',
    });
  });
});

function renderCapture() {
  let currentPath = '';
  function Probe() { currentPath = useLocation().pathname; return null; }
  const utils = render(
    <MemoryRouter initialEntries={['/capture']}>
      <Probe />
      <Routes>
        <Route path="/capture" element={<Capture />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/waiting/:jobId" element={<div>waiting-sentinel</div>} />
      </Routes>
    </MemoryRouter>
  );
  return { ...utils, getPath: () => currentPath };
}

describe('Capture (mobile camera)', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: true, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }));
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it('renders a file input with capture="environment" after the wizard step', async () => {
    renderCapture();
    // Advance the wizard step first.
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    const input = screen.getByLabelText(/camera|capture|photo/i) as HTMLInputElement;
    expect(input.type).toBe('file');
    expect(input.accept).toBe('image/*');
    expect(input.getAttribute('capture')).toBe('environment');
  });

  it('calls createScoringJob and navigates to /waiting/:jobId on file selection', async () => {
    const spy = vi.spyOn(api, 'createScoringJob').mockResolvedValue({ job_id: 'job-123', status: 'queued' });
    const { getPath } = renderCapture();
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    const input = screen.getByLabelText(/camera|capture|photo/i) as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], 'shot.jpg', { type: 'image/jpeg' });
    await userEvent.upload(input, file);
    await waitFor(() => expect(getPath()).toBe('/waiting/job-123'));
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe('Upload (PC file picker)', () => {
  it('renders a file input WITHOUT the capture attribute', async () => {
    render(
      <MemoryRouter initialEntries={['/upload']}>
        <Routes>
          <Route path="/upload" element={<Upload />} />
          <Route path="/waiting/:jobId" element={<div />} />
        </Routes>
      </MemoryRouter>
    );
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    const input = screen.getByLabelText(/file|upload|photo|select/i) as HTMLInputElement;
    expect(input.type).toBe('file');
    expect(input.accept).toBe('image/*');
    expect(input.getAttribute('capture')).toBeNull();
  });
});
