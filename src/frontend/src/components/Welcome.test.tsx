// Phase 3: Welcome component contract. The unauthenticated landing page.
// Pins the behaviors the App depends on: shows the app title, hero copy, and
// a Login control that fires onLogin. Hero copy is matched by its benefit-led
// substring (the exact wording can evolve; the promise to the shooter can't).
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Welcome } from './Welcome';

describe('Welcome', () => {
  it('renders the app title', () => {
    render(<Welcome onLogin={() => {}} />);
    expect(screen.getByRole('banner')).toHaveTextContent(/target-o-meter/i);
  });

  it('renders benefit-led hero copy for ISSF shooters', () => {
    render(<Welcome onLogin={() => {}} />);
    // The hero promises score + progress tracking — the exact wording may
    // evolve, but these two concepts must be present.
    const hero = screen.getByRole('region', { name: /hero|welcome|intro/i }) 
      ?? document.querySelector('main');
    expect((hero ?? document.body).textContent?.toLowerCase()).toMatch(/score/);
    expect((hero ?? document.body).textContent?.toLowerCase()).toMatch(/progress/);
  });

  it('renders a Login control in the top bar', () => {
    render(<Welcome onLogin={() => {}} />);
    expect(screen.getByRole('button', { name: /login/i })).toBeInTheDocument();
  });

  it('renders the ISSF target illustration', () => {
    render(<Welcome onLogin={() => {}} />);
    // The hero image is the ISSF target SVG, imported as a URL and rendered
    // as an <img> (Vite-hashed in prod). Assert on the accessible name.
    expect(screen.getByRole('img', { name: /issf.*target/i })).toBeInTheDocument();
  });

  it('calls onLogin when the Login button is clicked', async () => {
    const onLogin = vi.fn();
    render(<Welcome onLogin={onLogin} />);
    await userEvent.click(screen.getByRole('button', { name: /login/i }));
    expect(onLogin).toHaveBeenCalledTimes(1);
  });

  it('calls onLogin when the primary CTA is clicked', async () => {
    const onLogin = vi.fn();
    render(<Welcome onLogin={onLogin} />);
    // The hero CTA also triggers login.
    const cta = screen.getByRole('button', { name: /get started|sign in|start/i });
    await userEvent.click(cta);
    expect(onLogin).toHaveBeenCalledTimes(1);
  });
});
