// Phase 2: trivial Vitest smoke test — confirms Vitest + Testing Library +
// jsdom are wired and runnable. Expanded into real component tests in Phase 3.
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

describe('Phase 2 toolchain smoke', () => {
  it('renders the trivial hello', () => {
    render(<h1>Hello from React</h1>);
    expect(
      screen.getByRole('heading', { name: /hello from react/i })
    ).toBeInTheDocument();
  });
});
