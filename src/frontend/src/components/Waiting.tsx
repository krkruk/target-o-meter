// Waiting route — S-02 Phase 8 fills this in (polls getScoringJob until
// terminal, then navigates to /results/:jobId). Phase 6 ships a stub so the
// router table resolves.
export function Waiting() {
  return (
    <div data-testid="waiting-route">
      {/* TODO Phase 8: poll until terminal */}
    </div>
  );
}
