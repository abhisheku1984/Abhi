/** Typed API client. All calls go to the relative /api path (proxied in dev). */

export const API_BASE = '/api/v1';

export class ApiError extends Error {
  code: string;
  status: number;
  suggestedAction?: string;
  meta?: Record<string, unknown>;

  constructor(message: string, status: number, code: string, suggestedAction?: string,
              meta?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = code;
    this.suggestedAction = suggestedAction;
    this.meta = meta;
  }
}

let accessToken: string | null = localStorage.getItem('acs.token');
let onUnauthorized: (() => void) | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
  if (token) localStorage.setItem('acs.token', token);
  else localStorage.removeItem('acs.token');
}

export function getAccessToken() {
  return accessToken;
}

export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

type Options = {
  method?: string;
  body?: unknown;
  params?: Record<string, any>;
  signal?: AbortSignal;
  raw?: boolean;
};

async function request<T>(path: string, opts: Options = {}): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  Object.entries(opts.params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== '') url.searchParams.set(k, String(v));
  });

  const isForm = typeof FormData !== 'undefined' && opts.body instanceof FormData;
  const headers: Record<string, string> = {};
  if (!isForm) headers['Content-Type'] = 'application/json';
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;

  const res = await fetch(url.toString().replace(window.location.origin, ''), {
    method: opts.method || 'GET',
    headers,
    body: opts.body === undefined ? undefined : isForm ? (opts.body as FormData) : JSON.stringify(opts.body),
    signal: opts.signal,
  });

  if (res.status === 401) {
    setAccessToken(null);
    onUnauthorized?.();
  }

  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      // A proxy or server may answer with plain text — never let that become
      // an unhandled SyntaxError in the UI.
      data = null;
    }
  }

  if (!res.ok) {
    const err = data?.error ?? {};
    throw new ApiError(
      err.message || text.slice(0, 200) || `Request failed (${res.status})`,
      res.status,
      err.code || 'http_error',
      err.suggested_action,
      err.meta,
    );
  }
  return (opts.raw ? res : data) as T;
}

export const api = {
  get: <T>(p: string, params?: Options['params'], signal?: AbortSignal) => request<T>(p, { params, signal }),
  post: <T>(p: string, body?: unknown) => request<T>(p, { method: 'POST', body }),
  patch: <T>(p: string, body?: unknown) => request<T>(p, { method: 'PATCH', body }),
  put: <T>(p: string, body?: unknown) => request<T>(p, { method: 'PUT', body }),
  del: <T>(p: string, body?: unknown) => request<T>(p, { method: 'DELETE', body }),
  upload: <T>(p: string, form: FormData) => request<T>(p, { method: 'POST', body: form }),
};

/* ------------------------------------------------------------------ */
/* Domain types                                                        */
/* ------------------------------------------------------------------ */

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  storage_used_mb: number;
  storage_quota_mb: number;
}

export interface Job {
  id: string;
  type: string;
  mode: string;
  engine: string;
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  stage: string;
  params: Record<string, unknown>;
  result: Record<string, unknown>;
  error: { code?: string; message?: string; suggested_action?: string } | null;
  attempts: number;
  project_id: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds?: number | null;
}

export interface Asset {
  id: string;
  kind: 'image' | 'video' | 'audio' | 'avatar' | 'character' | 'document' | 'other';
  name: string;
  mime: string;
  size_bytes: number;
  width?: number | null;
  height?: number | null;
  duration_sec?: number | null;
  project_id: string | null;
  is_favorite: boolean;
  meta: Record<string, any>;
  url: string;
  thumbnail_url?: string | null;
  preview_url?: string | null;
  created_at: string | null;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  kind: string;
  status: string;
  is_favorite: boolean;
  thumbnail_url?: string | null;
  counts?: { assets: number; scenes: number; characters: number };
  created_at: string | null;
  updated_at: string | null;
}

export interface ModelInfo {
  id: string;
  display_name: string;
  family: string;
  version: string;
  provider: string;
  license: string;
  size_mb: number;
  vram_mb: number;
  speed: string;
  is_local: boolean;
  status: string;
  status_reason: string;
  modes: string[];
  capabilities: Record<string, any>;
  schema?: Record<string, any>;
  notes?: string;
}

export interface Branding {
  product_name: string;
  company_name: string;
  primary_color: string;
  secondary_color: string;
  theme: string;
  footer: string;
  logo_url: string;
  favicon_url: string;
  login_headline: string;
  login_subheadline: string;
}

/* ------------------------------------------------------------------ */
/* Endpoint helpers                                                    */
/* ------------------------------------------------------------------ */

export const endpoints = {
  login: (email: string, password: string) =>
    api.post<{ access_token: string; refresh_token: string; user: User }>('/auth/login', { email, password }),
  register: (email: string, password: string, name: string) =>
    api.post<{ access_token: string; user: User }>('/auth/register', { email, password, name }),
  me: () => api.get<User>('/auth/me'),
  updateMe: (body: Record<string, unknown>) => api.patch<User>('/auth/me', body),
  changePassword: (current_password: string, new_password: string) =>
    api.post<{ ok: boolean }>('/auth/password', { current_password, new_password }),

  projects: (params?: Record<string, unknown>) => api.get<{ items: Project[]; total: number }>('/projects', params),
  createProject: (body: Partial<Project>) => api.post<Project>('/projects', body),
  project: (id: string) => api.get<Project>(`/projects/${id}`),
  updateProject: (id: string, body: Partial<Project>) => api.patch<Project>(`/projects/${id}`, body),
  deleteProject: (id: string) => api.del<{ ok: boolean }>(`/projects/${id}`),
  projectAssets: (id: string, kind?: string) =>
    api.get<{ items: Asset[] }>(`/projects/${id}/assets`, { kind }),

  assets: (params?: Record<string, unknown>) => api.get<{ items: Asset[]; total: number }>('/assets', params),
  asset: (id: string) => api.get<Asset>(`/assets/${id}`),
  updateAsset: (id: string, body: Partial<Asset>) => api.patch<Asset>(`/assets/${id}`, body),
  deleteAsset: (id: string) => api.del<{ ok: boolean }>(`/assets/${id}`),
  duplicateAsset: (id: string) => api.post<Asset>(`/assets/${id}/duplicate`),
  uploadAsset: (form: FormData) => api.upload<Asset>('/assets/upload', form),
  assetStats: () => api.get<{ by_kind: Record<string, { count: number; bytes: number }>; total_mb: number }>(
    '/assets/stats/summary'),

  generate: (kind: string, body: Record<string, unknown>) =>
    api.post<{ job_id: string; engine: string; engine_name: string; status: string; estimate_seconds: number | null }>(
      `/generate/${kind}`, body),
  estimate: (body: Record<string, unknown>) =>
    api.post<{ seconds: number; vram_mb: number; validation: { ok: boolean; errors: string[]; warnings: string[] } }>(
      '/generate/estimate', body),
  modes: () => api.get<{ families: Record<string, ModelInfo[]> }>('/generate/modes'),

  jobs: (params?: Record<string, unknown>) => api.get<{ items: Job[]; total: number }>('/jobs', params),
  job: (id: string) => api.get<Job>(`/jobs/${id}`),
  cancelJob: (id: string) => api.post<{ id: string; status: string }>(`/jobs/${id}/cancel`),
  retryJob: (id: string) => api.post<{ id: string; status: string }>(`/jobs/${id}/retry`),
  jobStats: () => api.get<Record<string, number>>('/jobs/stats'),

  models: () => api.get<{ items: ModelInfo[]; gpu: Record<string, any>; recommended_profile: string }>('/models'),
  model: (id: string) => api.get<ModelInfo & { schema: Record<string, any> }>(`/models/${id}`),
  modelHealth: () => api.get<Record<string, any>>('/models/health'),
  activateModel: (id: string) => api.post<{ id: string; status: string }>(`/models/${id}/activate`),
  deactivateModel: (id: string) => api.post<{ id: string; status: string }>(`/models/${id}/deactivate`),
  testModel: (id: string) => api.post<Record<string, any>>(`/models/${id}/test`),
  installModel: (id: string, confirm = false) =>
    api.post<Record<string, any>>(`/models/${id}/install`, { confirm }),

  characters: (projectId?: string) => api.get<{ items: any[] }>('/characters', { project_id: projectId }),
  createCharacter: (body: Record<string, unknown>) => api.post<any>('/characters', body),
  updateCharacter: (id: string, body: Record<string, unknown>) => api.patch<any>(`/characters/${id}`, body),
  deleteCharacter: (id: string) => api.del<{ ok: boolean }>(`/characters/${id}`),
  characterPrompt: (id: string, body: Record<string, unknown>) =>
    api.post<{ locked_prompt: string; enhanced: string; structured: Record<string, string> }>(
      `/characters/${id}/prompt`, body),

  voices: (language?: string) => api.get<{ items: any[]; languages: string[] }>('/voices', { language }),
  createVoice: (body: Record<string, unknown>) => api.post<any>('/voices', body),
  updateVoice: (id: string, body: Record<string, unknown>) => api.patch<any>(`/voices/${id}`, body),
  deleteVoice: (id: string) => api.del<{ ok: boolean }>(`/voices/${id}`),

  avatars: () => api.get<{ items: any[] }>('/avatars'),
  createAvatar: (body: Record<string, unknown>) => api.post<any>('/avatars', body),
  updateAvatar: (id: string, body: Record<string, unknown>) => api.patch<any>(`/avatars/${id}`, body),
  deleteAvatar: (id: string) => api.del<{ ok: boolean }>(`/avatars/${id}`),

  generateStory: (body: Record<string, unknown>) => api.post<any>('/story/generate', body),
  story: (projectId: string) => api.get<{ scenes: any[] }>(`/story/${projectId}`),
  patchScene: (id: string, body: Record<string, unknown>) => api.patch<any>(`/story/scenes/${id}`, body),
  regenerateScene: (id: string, body?: Record<string, unknown>) =>
    api.post<{ job_id: string }>(`/story/scenes/${id}/regenerate`, body ?? {}),
  deleteScene: (id: string) => api.del<{ ok: boolean }>(`/story/scenes/${id}`),
  patchShot: (id: string, body: Record<string, unknown>) => api.patch<any>(`/story/shots/${id}`, body),
  renderStory: (projectId: string, body?: Record<string, unknown>) =>
    api.post<{ job_id: string }>(`/story/${projectId}/render`, body ?? {}),

  editorPresets: () => api.get<Record<string, any>>('/editor/presets'),
  renderTimeline: (body: Record<string, unknown>) => api.post<{ job_id: string }>('/editor/render', body),
  probeAsset: (assetId: string) => api.post<{ probe: Record<string, any> }>('/editor/probe', { asset_id: assetId }),

  workflows: () => api.get<{ items: any[] }>('/workflows'),
  workflowCatalog: () => api.get<{ node_types: Record<string, any> }>('/workflows/catalog'),
  createWorkflow: (body: Record<string, unknown>) => api.post<any>('/workflows', body),
  updateWorkflow: (id: string, body: Record<string, unknown>) => api.patch<any>(`/workflows/${id}`, body),
  runWorkflow: (id: string, graph?: Record<string, unknown>) =>
    api.post<{ job_id: string }>(`/workflows/${id}/run`, graph ? { graph } : {}),
  deleteWorkflow: (id: string) => api.del<{ ok: boolean }>(`/workflows/${id}`),

  transformPrompt: (prompt: string, kind: string, style?: string) =>
    api.post<{ original: string; enhanced: string; structured: Record<string, string> }>(
      '/prompts/transform', { prompt, kind, style }),
  promptTransforms: () => api.get<{ transforms: { id: string; label: string; description: string }[];
    styles: string[]; languages: Record<string, string> }>('/prompts/transforms'),
  prompts: () => api.get<{ items: any[] }>('/prompts'),
  savePrompt: (body: Record<string, unknown>) => api.post<any>('/prompts', body),

  publicSettings: () => api.get<{ branding: Branding; app: Record<string, string> }>('/settings/public'),
  branding: () => api.get<Branding>('/settings/branding'),
  updateBranding: (body: Partial<Branding>) => api.put<Branding>('/settings/branding', body),
  providers: () => api.get<{ providers: { id: string; configured: boolean; env: string[] }[] }>('/settings/providers'),
  moderation: () => api.get<Record<string, unknown>>('/settings/moderation'),
  updateModeration: (body: Record<string, unknown>) => api.put<Record<string, unknown>>('/settings/moderation', body),

  adminOverview: () => api.get<Record<string, any>>('/admin/overview'),
  adminUsers: () => api.get<{ items: any[] }>('/admin/users'),
  adminJobs: (status?: string) => api.get<{ items: Job[]; stats: Record<string, number> }>('/admin/jobs', { status }),
  adminLogs: (lines = 200) => api.get<{ items: string[] }>('/admin/logs', { lines }),
  adminHealth: () => api.get<Record<string, any>>('/admin/system-health'),
  adminAudit: () => api.get<{ items: any[] }>('/admin/audit'),
  adminFlags: () => api.get<{ items: { key: string; enabled: boolean }[] }>('/admin/feature-flags'),
  setFlag: (body: { key: string; enabled: boolean }) => api.put<{ key: string; enabled: boolean }>(
    '/admin/feature-flags', body),
  updateUser: (id: string, body: Record<string, unknown>) => api.patch<any>(`/admin/users/${id}`, body),

  assistant: (command: string, context?: Record<string, unknown>, confirm = false, dryRun = false) =>
    api.post<any>('/assistant/command', { command, context, confirm, dry_run: dryRun }),

  health: () => api.get<Record<string, any>>('/health'),
  engineHealth: () => api.get<{ engines: ModelInfo[] }>('/health/engines'),
  gpu: () => api.get<Record<string, any>>('/health/gpu'),
};

export function fileUrl(path?: string | null) {
  if (!path) return undefined;
  return path.startsWith('http') ? path : `${path}`;
}
