/**
 * Typed API client.
 *
 * One rule enforced here: capability responses always carry a `mode` of
 * `real` | `demo` | `not_configured`, and the UI must render that verbatim.
 * Nothing in this app may present a `demo` engine as AI generation.
 */

export type Mode = "real" | "demo" | "not_configured" | "unavailable";

export interface CapabilityProvider {
  id: string;
  label: string;
  capability: string;
  kind: "local" | "remote" | "cloud";
  quality: "real" | "demo";
  engine: string;
  requires: string[];
  describes: string;
  docs_url: string;
  available: boolean;
  reason: string;
}

export interface CapabilityEntry {
  selected: string;
  selected_label: string;
  usable: boolean;
  quality: string;
  mode: Mode;
  selectable: boolean;
  providers: CapabilityProvider[];
}

export interface Asset {
  id: string;
  project_id: string | null;
  kind: "image" | "video" | "audio" | "file";
  filename: string;
  rel_path: string;
  thumb_path: string | null;
  mime: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  duration_s: number | null;
  provider: string;
  engine: string;
  prompt: string;
  params: Record<string, unknown>;
  meta: Record<string, any>;
  parent_asset_id: string | null;
  source_job_id: string | null;
  created_at: string;
  url: string;
  thumb_url: string | null;
  download_url: string;
}

export interface Job {
  id: string;
  project_id: string | null;
  kind: string;
  label: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  error: string | null;
  attempts: number;
  max_attempts: number;
  params: Record<string, any>;
  result: Record<string, any>;
  logs?: { t: string; message: string }[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  style_prompt: string;
  aspect_ratio: string;
  status: string;
  created_at: string;
  updated_at: string;
  asset_counts: Record<string, number>;
  running_jobs: number;
  story_count: number;
  character_count: number;
  shot_count: number;
  recent_assets: Asset[];
}

export interface Character {
  id: string;
  project_id: string;
  name: string;
  role: string;
  description: string;
  appearance: string;
  style: string;
  seed: number;
  ref_asset_ids: string[];
  reference_assets: Asset[];
}

export interface Shot {
  id: string;
  story_id: string;
  index_no: number;
  scene_no: number;
  title: string;
  description: string;
  prompt: string;
  duration_s: number;
  motion: string;
  transition: string;
  caption: string;
  status: string;
  character_ids: string[];
  image: Asset | null;
  audio?: Asset | null;
}

export interface Storyboard {
  story: { id: string; title: string; logline: string; engine: string; scenes: any[]; status: string };
  shots: Shot[];
  stats: { shot_count: number; with_images: number; with_audio: number; total_duration_s: number };
}

export interface Story {
  id: string;
  project_id: string;
  title: string;
  logline: string;
  engine: string;
  status: string;
  scenes: any[];
  shots: Shot[];
  shot_count: number;
  generated_shots: number;
  total_duration_s: number;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  project_id: string | null;
  graph: { nodes: any[]; edges: any[] };
  validation: { valid: boolean; errors: string[]; warnings: string[]; order: string[] };
  node_count: number;
  runs: { id: string; status: string; created_at: string }[];
}

export interface SystemStatus {
  app: { name: string; version: string; uptime_s: number };
  cpu: { cores: number; load_avg: number[] | null; platform: string };
  memory: { total_gb: number | null; available_gb: number | null };
  gpu: { available: boolean; reason: string | null; devices: any[] };
  ffmpeg: { available: boolean; version: string | null; binary: string | null };
  storage: { data_dir: string; disk_total_gb: number; disk_free_gb: number; media_store_mb: number };
  capabilities: Record<string, Mode>;
  providers: Record<string, string>;
  jobs: { counts: Record<string, number>; running: any[]; workers: number; configured_workers: number };
  job_kinds: string[];
  python_packages: Record<string, string | null>;
}

export class ApiError extends Error {
  status: number;
  payload: any;
  constructor(status: number, message: string, payload: any) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

const TOKEN_KEY = "abhi.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  const text = await response.text();
  let payload: any = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = text;
  }
  if (!response.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.error || detail?.detail || detail?.message || payload?.error || `Request failed (${response.status})`;
    throw new ApiError(response.status, message, payload);
  }
  return payload as T;
}

const json = (body: unknown) => ({ body: JSON.stringify(body) });

export const api = {
  // ---- auth ----
  login: (username: string, password: string) =>
    request<{ token: string; expires_at: string; user: any }>("/api/auth/login", {
      method: "POST",
      ...json({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  me: () => request<{ user: any; settings: any }>("/api/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    request<{ ok: boolean }>("/api/auth/password", { method: "POST", ...json({ current_password, new_password }) }),
  listKeys: () => request<{ items: any[] }>("/api/auth/keys"),
  createKey: (name: string) => request<any>("/api/auth/keys", { method: "POST", ...json({ name }) }),
  deleteKey: (id: string) => request<{ ok: boolean }>(`/api/auth/keys/${id}`, { method: "DELETE" }),

  // ---- system ----
  health: () => request<any>("/api/health"),
  systemStatus: () => request<SystemStatus>("/api/system/status"),
  capabilities: () =>
    request<{ modes: string[]; capabilities: Record<string, CapabilityEntry>; legend: Record<string, string> }>(
      "/api/system/capabilities",
    ),

  // ---- projects ----
  listProjects: () => request<{ items: Project[]; total: number }>("/api/projects"),
  createProject: (body: { name: string; description?: string; style_prompt?: string; aspect_ratio?: string }) =>
    request<Project>("/api/projects", { method: "POST", ...json(body) }),
  getProject: (id: string) => request<Project>(`/api/projects/${id}`),
  overview: (id: string) => request<any>(`/api/projects/${id}/overview`),
  updateProject: (id: string, body: Partial<Project>) =>
    request<Project>(`/api/projects/${id}`, { method: "PATCH", ...json(body) }),
  deleteProject: (id: string) => request<any>(`/api/projects/${id}`, { method: "DELETE" }),

  // ---- characters ----
  listCharacters: (projectId: string) => request<{ items: Character[] }>(`/api/projects/${projectId}/characters`),
  createCharacter: (projectId: string, body: Partial<Character>) =>
    request<Character>(`/api/projects/${projectId}/characters`, { method: "POST", ...json(body) }),
  updateCharacter: (id: string, body: Partial<Character>) =>
    request<Character>(`/api/characters/${id}`, { method: "PATCH", ...json(body) }),
  deleteCharacter: (id: string) => request<any>(`/api/characters/${id}`, { method: "DELETE" }),
  characterReferences: (id: string, body: { prompt?: string; count?: number; width?: number; height?: number }) =>
    request<any>(`/api/characters/${id}/references?wait=true`, { method: "POST", ...json(body) }),
  characterSheet: (id: string) => request<any>(`/api/characters/${id}/sheet?wait=true`, { method: "POST" }),

  // ---- stories ----
  listStories: (projectId: string) => request<{ items: Story[] }>(`/api/projects/${projectId}/stories`),
  storyboard: (storyId: string) => request<Storyboard>(`/api/stories/${storyId}/storyboard`),
  updateShot: (shotId: string, body: Partial<Shot>) =>
    request<Shot>(`/api/stories/shots/${shotId}`, { method: "PATCH", ...json(body) }),
  addShot: (storyId: string, body: Partial<Shot>) =>
    request<any>(`/api/stories/${storyId}/shots`, { method: "POST", ...json(body) }),
  deleteShot: (shotId: string) => request<any>(`/api/stories/shots/${shotId}`, { method: "DELETE" }),
  generateShot: (shotId: string, wait = true) =>
    request<any>(`/api/generate/shot/${shotId}${wait ? "?wait=true" : ""}`, { method: "POST" }),
  shotVoice: (shotId: string, voice: string) =>
    request<any>(`/api/stories/shots/${shotId}/voice?voice=${encodeURIComponent(voice)}`, { method: "POST" }),
  generateStoryShots: (storyId: string, body: { shot_ids?: string[]; only_missing?: boolean; narration?: boolean; voice?: string }) =>
    request<any>(`/api/stories/${storyId}/generate`, { method: "POST", ...json(body) }),
  renderStory: (storyId: string, params: Record<string, any> = {}, wait = true) => {
    const query = new URLSearchParams({ wait: String(wait), ...Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])) });
    return request<any>(`/api/stories/${storyId}/render?${query}`, { method: "POST" });
  },

  // ---- assets ----
  listAssets: (params: Record<string, any> = {}) => {
    const query = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [k, String(v)])),
    );
    return request<{ items: Asset[]; total: number }>(`/api/assets?${query}`);
  },
  getAsset: (id: string) => request<Asset & { derived_assets: Asset[]; related_jobs: Job[] }>(`/api/assets/${id}`),
  assetStats: (projectId?: string) =>
    request<{ counts: Record<string, number>; by_engine: any[] }>(`/api/assets/stats${projectId ? `?project_id=${projectId}` : ""}`),
  editAsset: (id: string, ops: any[], format = "png") =>
    request<any>(`/api/assets/${id}/edit`, { method: "POST", ...json({ ops, format }) }),
  deleteAsset: (id: string, hard = false) => request<any>(`/api/assets/${id}?hard=${hard}`, { method: "DELETE" }),
  rebuildThumbnail: (id: string) => request<any>(`/api/assets/${id}/thumbnail`, { method: "POST" }),
  uploadAsset: async (file: File, projectId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (projectId) form.append("project_id", projectId);
    return request<Asset>("/api/assets/upload", { method: "POST", body: form });
  },

  // ---- jobs ----
  listJobs: (params: Record<string, any> = {}) => {
    const query = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [k, String(v)])),
    );
    return request<{ items: Job[]; total: number }>(`/api/jobs?${query}`);
  },
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  jobStats: () => request<any>("/api/jobs/stats"),
  jobKinds: () => request<{ kinds: { kind: string; doc: string }[] }>("/api/jobs/kinds"),
  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  retryJob: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: "POST" }),
  deleteJob: (id: string) => request<any>(`/api/jobs/${id}`, { method: "DELETE" }),

  // ---- generation ----
  generateImage: (body: any, wait = false) =>
    request<any>(`/api/generate/image${wait ? "?wait=true" : ""}`, { method: "POST", ...json(body) }),
  generateVideo: (body: any, wait = false) =>
    request<any>(`/api/generate/video${wait ? "?wait=true" : ""}`, { method: "POST", ...json(body) }),
  renderVideo: (body: any, wait = false) =>
    request<any>(`/api/generate/video/render${wait ? "?wait=true" : ""}`, { method: "POST", ...json(body) }),
  editVideo: (body: any, wait = false) =>
    request<any>(`/api/generate/video/edit${wait ? "?wait=true" : ""}`, { method: "POST", ...json(body) }),
  muxVideo: (body: any, wait = false) =>
    request<any>(`/api/generate/video/mux${wait ? "?wait=true" : ""}`, { method: "POST", ...json(body) }),
  generateVoice: (body: any, wait = false) =>
    request<any>(`/api/generate/voice${wait ? "?wait=true" : ""}`, { method: "POST", ...json(body) }),
  generateStory: (body: any, wait = true) =>
    request<any>(`/api/generate/story${wait ? "?wait=true" : ""}`, { method: "POST", ...json(body) }),
  lipsync: (body: any, wait = false) =>
    request<any>(`/api/generate/lipsync${wait ? "?wait=true" : ""}`, { method: "POST", ...json(body) }),
  demoPipeline: (projectId?: string) =>
    request<any>(`/api/generate/demo?wait=true${projectId ? `&project_id=${projectId}` : ""}`, { method: "POST" }),

  // ---- workflows ----
  listWorkflows: (projectId?: string) =>
    request<{ items: Workflow[] }>(`/api/workflows${projectId ? `?project_id=${projectId}` : ""}`),
  createWorkflow: (body: { name: string; description?: string; project_id?: string | null; graph: any }) =>
    request<Workflow>("/api/workflows", { method: "POST", ...json(body) }),
  getWorkflow: (id: string) => request<Workflow>(`/api/workflows/${id}`),
  updateWorkflow: (id: string, body: any) => request<Workflow>(`/api/workflows/${id}`, { method: "PATCH", ...json(body) }),
  deleteWorkflow: (id: string) => request<any>(`/api/workflows/${id}`, { method: "DELETE" }),
  validateWorkflow: (id: string) => request<any>(`/api/workflows/${id}/validate`, { method: "POST" }),
  runWorkflow: (id: string, body: { variables?: Record<string, any>; dry_run?: boolean }) =>
    request<any>(`/api/workflows/${id}/run`, { method: "POST", ...json(body) }),
};

/** Poll a job until it reaches a terminal state. */
export async function pollJob(
  jobId: string,
  onTick?: (job: Job) => void,
  { intervalMs = 800, timeoutMs = 15 * 60 * 1000 } = {},
): Promise<Job> {
  const deadline = Date.now() + timeoutMs;
  let job = await api.getJob(jobId);
  onTick?.(job);
  while (!["succeeded", "failed", "cancelled"].includes(job.status)) {
    if (Date.now() > deadline) return job;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    job = await api.getJob(jobId);
    onTick?.(job);
  }
  return job;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatDuration(seconds?: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export function timeAgo(iso: string): string {
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).getTime();
  const seconds = Math.max(1, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
