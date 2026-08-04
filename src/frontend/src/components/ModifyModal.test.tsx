// user-score-dashboard Phase 4: <ModifyModal> + <DeleteModal> contract.
//
// Pins the mutation modals:
//   - ModifyModal pre-fills from getScore (the accepted snapshot); Modify
//     submits updateScore then calls onModified + onClose; Cancel calls onClose
//     with no API call; a non-ok PATCH surfaces an inline error.
//   - DeleteModal confirm calls deleteScore then onDeleted + onClose; Cancel
//     calls onClose with no API call.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as api from '../api';
import { ModifyModal } from './ModifyModal';
import { DeleteModal } from './DeleteModal';
import type { ResultSummary, AcceptedResult } from '../api';

const ROW: ResultSummary = {
  result_id: 'r1', source_job: 'job-1', created_at: '2026-08-04T10:00:00Z',
  score_average: 9.0, hole_count: 1, target_type: 'air_pistol',
};

const SNAPSHOT: AcceptedResult = {
  result_id: 'r1', source_job: 'job-1', target_type: 'air_pistol',
  caliber_hint: '9x19mm', distance: 25, weapon_type: 'sport_pistol',
  holes: [{ x: 0, y: 0, score: 9, confidence: 1.0 }], score_average: 9.0,
  created_at: '2026-08-04T10:00:00Z',
};

describe('<ModifyModal>', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('pre-fills the holes + params from getScore, then Modify submits updateScore', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getScore').mockResolvedValue(SNAPSHOT);
    const updateSpy = vi.spyOn(api, 'updateScore').mockResolvedValue(SNAPSHOT);
    const onModified = vi.fn();
    const onClose = vi.fn();
    render(<ModifyModal result={ROW} onClose={onClose} onModified={onModified} />);

    // Wait for the snapshot to load (the form appears).
    expect(await screen.findByText(/holes/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^modify$/i }));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy.mock.calls[0][0]).toBe('r1');
    // The patched holes carry the score (9 here — no correction changed).
    expect(updateSpy.mock.calls[0][1].holes).toEqual([
      expect.objectContaining({ x: 0, y: 0, score: 9 }),
    ]);
    expect(onModified).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Cancel calls onClose with no updateScore call', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getScore').mockResolvedValue(SNAPSHOT);
    const updateSpy = vi.spyOn(api, 'updateScore').mockResolvedValue(SNAPSHOT);
    const onClose = vi.fn();
    render(<ModifyModal result={ROW} onClose={onClose} onModified={vi.fn()} />);

    await screen.findByText(/holes/i);
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it('surfaces an inline error on a failed PATCH (404)', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getScore').mockResolvedValue(SNAPSHOT);
    vi.spyOn(api, 'updateScore').mockRejectedValue(new api.HttpError(404, 'not found'));
    const onClose = vi.fn();
    render(<ModifyModal result={ROW} onClose={onClose} onModified={vi.fn()} />);

    await screen.findByText(/holes/i);
    await user.click(screen.getByRole('button', { name: /^modify$/i }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled(); // stays open on error
  });
});

describe('<DeleteModal>', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('confirm calls deleteScore then onDeleted + onClose', async () => {
    const user = userEvent.setup();
    const deleteSpy = vi.spyOn(api, 'deleteScore').mockResolvedValue(undefined);
    const onDeleted = vi.fn();
    const onClose = vi.fn();
    render(<DeleteModal result={ROW} onClose={onClose} onDeleted={onDeleted} />);

    await user.click(screen.getByRole('button', { name: /delete permanently/i }));
    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith('r1'));
    expect(onDeleted).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Cancel calls onClose with no deleteScore call', async () => {
    const user = userEvent.setup();
    const deleteSpy = vi.spyOn(api, 'deleteScore').mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<DeleteModal result={ROW} onClose={onClose} onDeleted={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it('surfaces an inline error on a failed DELETE (404)', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'deleteScore').mockRejectedValue(new api.HttpError(404, 'not found'));
    const onClose = vi.fn();
    render(<DeleteModal result={ROW} onClose={onClose} onDeleted={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /delete permanently/i }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
