import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, getAccessToken, setAccessToken, setUnauthorizedHandler } from '@/lib/api';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('api client', () => {
  beforeEach(() => {
    localStorage.clear();
    setAccessToken(null);
    setUnauthorizedHandler(null);
    vi.stubGlobal('location', { origin: 'http://localhost:5173' });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('sends the bearer token on authenticated calls', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    setAccessToken('token-123');

    await api.get('/projects');

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe('Bearer token-123');
    expect(getAccessToken()).toBe('token-123');
  });

  it('surfaces the friendly server error, not a stack trace', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: 'model_not_installed',
              message: 'Model not installed: diffusers-image',
              suggested_action: 'Install it from Model Manager.',
              error_id: 'err_123',
            },
          },
          409,
        ),
      ),
    );

    await expect(api.post('/generate/image', { prompt: 'x' })).rejects.toMatchObject({
      status: 409,
      code: 'model_not_installed',
      message: 'Model not installed: diffusers-image',
      suggestedAction: 'Install it from Model Manager.',
    });
  });

  it('throws an ApiError with a generic message when the body is not JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('boom', { status: 500 })),
    );
    await expect(api.get('/jobs')).rejects.toBeInstanceOf(ApiError);
  });

  it('clears the session and notifies on 401', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setAccessToken('expired');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ error: { code: 'unauthorized' } }, 401)),
    );

    await expect(api.get('/auth/me')).rejects.toBeInstanceOf(ApiError);
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem('acs.token')).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it('encodes query params and skips empty values', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await api.get('/assets', { kind: 'image', project_id: '', q: undefined });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/v1/assets?kind=image');
    expect(url).not.toContain('project_id');
    expect(url).not.toContain('q=');
  });
});
