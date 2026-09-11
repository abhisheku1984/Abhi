import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, getToken, setToken, type SystemStatus } from "./api";
import { usePolling, useToast } from "./components/ui";
import Dashboard from "./pages/Dashboard";
import Projects, { ProjectDetail } from "./pages/Projects";
import Library from "./pages/Library";
import ImageStudio from "./pages/ImageStudio";
import VideoStudio from "./pages/VideoStudio";
import StoryStudio from "./pages/StoryStudio";
import Workflows from "./pages/Workflows";
import JobsPage from "./pages/Jobs";
import SystemPage from "./pages/System";

const NAV = [
  { to: "/", label: "Dashboard", icon: "◈", end: true },
  { to: "/projects", label: "Projects", icon: "▤" },
  { to: "/library", label: "Library", icon: "▦" },
  { to: "/image", label: "Image Studio", icon: "✦" },
  { to: "/video", label: "Video Studio", icon: "▶" },
  { to: "/story", label: "Story Studio", icon: "✎" },
  { to: "/workflows", label: "Workflows", icon: "⛓" },
  { to: "/jobs", label: "Jobs", icon: "⚙" },
  { to: "/system", label: "System & Models", icon: "⌗" },
];

function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.login(username, password);
      setToken(result.token);
      onDone();
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center" style={{ minHeight: "100vh", padding: 20 }}>
      <form className="card" style={{ width: 360 }} onSubmit={submit}>
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <div className="brand-name">Abhi Studio</div>
            <div className="brand-sub">local-first media studio</div>
          </div>
        </div>
        <div className="field">
          <label>Username</label>
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            autoFocus
          />
        </div>
        {error && <div className="banner error" style={{ marginBottom: 12 }}>{error}</div>}
        <button className="primary" style={{ width: "100%" }} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="faint" style={{ marginTop: 12, marginBottom: 0 }}>
          Default credentials come from <span className="mono">ABHI_ADMIN_USER</span> /{" "}
          <span className="mono">ABHI_ADMIN_PASSWORD</span> in <span className="mono">.env</span>.
        </p>
      </form>
    </div>
  );
}

function JobTicker({ status }: { status: SystemStatus | null }) {
  const running = status?.jobs?.running ?? [];
  if (!running.length) return null;
  return (
    <div className="row" style={{ gap: 8 }}>
      {running.slice(0, 3).map((job) => (
        <span key={job.id} className="badge running" title={job.label}>
          {job.kind} {Math.round((job.progress || 0) * 100)}%
        </span>
      ))}
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [user, setUser] = useState<any>(null);
  const navigate = useNavigate();
  const { push } = useToast();

  const bootstrap = useCallback(async () => {
    if (!getToken()) {
      setAuthed(false);
      return;
    }
    try {
      const me = await api.me();
      setUser(me.user);
      setAuthed(true);
      setStatus(await api.systemStatus());
    } catch {
      setToken(null);
      setAuthed(false);
    }
  }, []);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  const refresh = useCallback(() => {
    if (!authed) return;
    api.systemStatus().then(setStatus).catch(() => {});
  }, [authed]);
  usePolling(refresh, 5000, Boolean(authed));

  async function logout() {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    setToken(null);
    setAuthed(false);
    push("info", "Signed out");
  }

  if (authed === null) {
    return (
      <div className="center" style={{ minHeight: "100vh" }}>
        <span className="spinner" />
      </div>
    );
  }
  if (!authed) return <Login onDone={bootstrap} />;

  const caps = status?.capabilities ?? {};
  const demoCaps = Object.entries(caps).filter(([key, mode]) => mode === "demo" && key !== "image_util").map(([key]) => key);
  const missingCaps = Object.entries(caps).filter(([, mode]) => mode === "not_configured").map(([key]) => key);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <div className="brand-name">Abhi Studio</div>
            <div className="brand-sub">v{status?.app.version ?? "0.1.0"}</div>
          </div>
        </div>
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <span className="ico">{item.icon}</span>
            <span className="label">{item.label}</span>
          </NavLink>
        ))}
        <div style={{ flex: 1 }} />
        <div className="faint" style={{ padding: "8px 11px" }}>
          {user?.username}
          <button className="ghost sm" style={{ marginTop: 6, width: "100%" }} onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <strong>{status?.app.name ?? "Abhi Studio"}</strong>
          <JobTicker status={status} />
          <div className="spacer" />
          {missingCaps.length > 0 && (
            <span className="badge not_configured" title={`No provider configured yet for: ${missingCaps.join(", ")}`}>
              ⚠ {missingCaps.join(" / ")} not configured
            </span>
          )}
          <span className="badge" title={status?.ffmpeg.available ? `FFmpeg ${status?.ffmpeg.version}` : "FFmpeg missing"}>
            ffmpeg {status?.ffmpeg.available ? status?.ffmpeg.version : "✗"}
          </span>
          <span className="badge" title={status?.gpu.available ? "GPU detected" : status?.gpu.reason ?? "No GPU"}>
            gpu {status?.gpu.available ? "✓" : "✗"}
          </span>
        </div>

        {demoCaps.length > 0 && (
          <div className="page" style={{ paddingBottom: 0 }}>
            <div className="banner warn">
              <span>
                <strong>Honest capability notice:</strong> {demoCaps.join(", ")} currently run on local DEMO engines (real
                algorithms, not neural models).{" "}
                <a
                  href="/system"
                  onClick={(event) => {
                    event.preventDefault();
                    navigate("/system");
                  }}
                >
                  Configure a cloud or GPU provider
                </a>{" "}
                for model-grade output.
              </span>
            </div>
          </div>
        )}

        <Routes>
          <Route path="/" element={<Dashboard status={status} refresh={refresh} />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/library" element={<Library />} />
          <Route path="/image" element={<ImageStudio />} />
          <Route path="/video" element={<VideoStudio />} />
          <Route path="/story" element={<StoryStudio />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/system" element={<SystemPage status={status} user={user} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
