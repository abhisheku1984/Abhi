import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const { login, navigate } = vi.hoisted(() => ({ login: vi.fn(), navigate: vi.fn() }));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    endpoints: { ...actual.endpoints, login },
    setAccessToken: vi.fn(),
  };
});

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

import { LoginPage } from '@/features/auth/LoginPage';

describe('LoginPage', () => {
  beforeEach(() => {
    login.mockReset();
    navigate.mockReset();
  });

  it('renders the sign-in form and submits real credentials', async () => {
    login.mockResolvedValue({
      access_token: 'tok',
      refresh_token: 'ref',
      user: { id: 'usr_1', email: 'admin@studio.ai', name: 'Studio Admin', role: 'owner' },
    });

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    const submit = screen.getByRole('button', { name: /sign in/i });
    await userEvent.click(submit);

    await waitFor(() => expect(login).toHaveBeenCalledWith('admin@studio.ai', 'Admin@12345'));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/dashboard'));
  });

  it('shows the server message when sign-in fails, without a stack trace', async () => {
    login.mockRejectedValue(new Error('Invalid email or password'));

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('Invalid email or password')).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});
