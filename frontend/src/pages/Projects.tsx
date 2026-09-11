import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, timeAgo, type Asset, type Character, type Project, type Story } from "../api";
import { AssetTile, Card, Empty, Modal, Spinner, StatusBadge, usePolling, useToast } from "../components/ui";

export default function Projects() {
  const [items, setItems] = useState<Project[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", style_prompt: "", aspect_ratio: "16:9" });
  const [busy, setBusy] = useState(false);
  const { push } = useToast();
  const navigate = useNavigate();

  const load = useCallback(() => {
    api.listProjects().then((data) => setItems(data.items)).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!form.name.trim()) return;
    setBusy(true);
    try {
      const project = await api.createProject(form);
      push("ok", `Project “${project.name}” created`);
      setOpen(false);
      setForm({ name: "", description: "", style_prompt: "", aspect_ratio: "16:9" });
      navigate(`/projects/${project.id}`);
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <h1>Projects</h1>
        <button className="primary" onClick={() => setOpen(true)}>
          + New project
        </button>
      </div>

      {items.length === 0 ? (
        <Card>
          <Empty>No projects yet. A project groups characters, stories, shots and generated assets.</Empty>
        </Card>
      ) : (
        <div className="grid cols-3">
          {items.map((project) => (
            <div key={project.id} className="card" style={{ cursor: "pointer" }} onClick={() => navigate(`/projects/${project.id}`)}>
              <div className="spread">
                <h2 style={{ margin: 0 }}>{project.name}</h2>
                <span className="badge">{project.asset_counts?.total ?? 0} assets</span>
              </div>
              {project.description && <p className="muted" style={{ marginTop: 8 }}>{project.description}</p>}
              <div className="chip-row" style={{ marginTop: 10 }}>
                <span className="faint">{project.story_count} stories</span>
                <span className="faint">{project.character_count} characters</span>
                <span className="faint">{project.shot_count} shots</span>
                <span className="faint">updated {timeAgo(project.updated_at)}</span>
              </div>
              {project.recent_assets?.length ? (
                <div className="row" style={{ marginTop: 12, gap: 6 }}>
                  {project.recent_assets.slice(0, 4).map((asset) => (
                    <img
                      key={asset.id}
                      src={asset.thumb_url || asset.url}
                      alt=""
                      style={{ width: 52, height: 34, objectFit: "cover", borderRadius: 6, border: "1px solid var(--line)" }}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}

      {open && (
        <Modal title="New project" onClose={() => setOpen(false)}>
          <form onSubmit={create}>
            <div className="field">
              <label>Name</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus required />
            </div>
            <div className="field">
              <label>Description</label>
              <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="field">
              <label>Global style prompt (appended to every generation in this project)</label>
              <input
                value={form.style_prompt}
                placeholder="cinematic, 35mm, volumetric light"
                onChange={(e) => setForm({ ...form, style_prompt: e.target.value })}
              />
            </div>
            <div className="field">
              <label>Aspect ratio</label>
              <select value={form.aspect_ratio} onChange={(e) => setForm({ ...form, aspect_ratio: e.target.value })}>
                <option value="16:9">16:9 landscape</option>
                <option value="9:16">9:16 vertical</option>
                <option value="1:1">1:1 square</option>
                <option value="4:5">4:5 portrait</option>
              </select>
            </div>
            <button className="primary" disabled={busy}>
              {busy ? "Creating…" : "Create project"}
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------ */
export function ProjectDetail() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const { push } = useToast();
  const [overview, setOverview] = useState<any>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [stories, setStories] = useState<Story[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [tab, setTab] = useState<"overview" | "characters" | "stories" | "assets">("overview");
  const [charForm, setCharForm] = useState({ name: "", role: "", appearance: "", style: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [ov, chars, storyList, assetList] = await Promise.all([
        api.overview(projectId),
        api.listCharacters(projectId),
        api.listStories(projectId),
        api.listAssets({ project_id: projectId, limit: 60 }),
      ]);
      setOverview(ov);
      setCharacters(chars.items);
      setStories(storyList.items);
      setAssets(assetList.items);
    } catch (error: any) {
      push("error", error.message);
    }
  }, [projectId, push]);

  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, 7000);

  async function addCharacter(event: React.FormEvent) {
    event.preventDefault();
    if (!charForm.name.trim()) return;
    setBusy(true);
    try {
      await api.createCharacter(projectId, charForm);
      setCharForm({ name: "", role: "", appearance: "", style: "" });
      push("ok", "Character added with a locked seed");
      load();
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  async function generateReference(character: Character) {
    try {
      const result = await api.characterReferences(character.id, { count: 1, width: 768, height: 1024 });
      push("ok", `Reference generated for ${character.name}`);
      load();
      void result;
    } catch (error: any) {
      push("error", error.message);
    }
  }

  const project = overview?.project;

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <div>
          <button className="ghost sm" onClick={() => navigate("/projects")}>
            ← All projects
          </button>
          <h1 style={{ marginTop: 6 }}>{project?.name ?? "Project"}</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            {project?.description || "No description"} {project?.style_prompt ? `· style: ${project.style_prompt}` : ""}
          </p>
        </div>
        <div className="row">
          <button onClick={() => navigate(`/story?project=${projectId}`)}>✎ New story</button>
          <button className="primary" onClick={() => navigate(`/image?project=${projectId}`)}>
            ✦ Generate image
          </button>
        </div>
      </div>

      <div className="row">
        {(["overview", "characters", "stories", "assets"] as const).map((name) => (
          <button key={name} className={tab === name ? "primary" : ""} onClick={() => setTab(name)}>
            {name} {name === "characters" ? `(${characters.length})` : name === "stories" ? `(${stories.length})` : name === "assets" ? `(${assets.length})` : ""}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid cols-2">
          <Card title="Asset breakdown">
            <div className="grid cols-2">
              {Object.entries(overview?.asset_counts ?? {}).map(([kind, count]) => (
                <div className="stat" key={kind}>
                  <span className="stat-value">{count as number}</span>
                  <span className="stat-label">{kind}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card title="Recent jobs">
            {overview?.jobs?.length ? (
              <table>
                <tbody>
                  {overview.jobs.map((job: any) => (
                    <tr key={job.id}>
                      <td className="truncate">{job.label}</td>
                      <td>
                        <StatusBadge status={job.status} />
                      </td>
                      <td className="faint">{timeAgo(job.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>No jobs yet in this project.</Empty>
            )}
          </Card>
        </div>
      )}

      {tab === "characters" && (
        <div className="grid cols-2">
          <Card title="Cast">
            {characters.length === 0 ? (
              <Empty>No characters yet. Locked characters keep appearance consistent across shots.</Empty>
            ) : (
              <div className="stack">
                {characters.map((character) => (
                  <div key={character.id} className="card tight">
                    <div className="spread">
                      <div>
                        <strong>{character.name}</strong> {character.role && <span className="faint">· {character.role}</span>}
                        <div className="faint">seed {character.seed}</div>
                      </div>
                      <div className="row">
                        <button className="sm" onClick={() => generateReference(character)}>
                          + Reference
                        </button>
                        <button
                          className="sm"
                          onClick={async () => {
                            try {
                              await api.characterSheet(character.id);
                              push("ok", "Character sheet built");
                              load();
                            } catch (error: any) {
                              push("error", error.message);
                            }
                          }}
                        >
                          Sheet
                        </button>
                      </div>
                    </div>
                    {character.appearance && <p className="muted" style={{ marginTop: 8, marginBottom: 0 }}>{character.appearance}</p>}
                    {character.reference_assets?.length ? (
                      <div className="row" style={{ marginTop: 10, gap: 6 }}>
                        {character.reference_assets.slice(0, 6).map((asset) => (
                          <img key={asset.id} src={asset.thumb_url || asset.url} alt="" style={{ width: 68, height: 68, objectFit: "cover", borderRadius: 8 }} />
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Card>
          <Card title="Add character">
            <form onSubmit={addCharacter}>
              <div className="field">
                <label>Name</label>
                <input value={charForm.name} onChange={(e) => setCharForm({ ...charForm, name: e.target.value })} required />
              </div>
              <div className="field">
                <label>Role</label>
                <input value={charForm.role} onChange={(e) => setCharForm({ ...charForm, role: e.target.value })} />
              </div>
              <div className="field">
                <label>Appearance (injected into every shot prompt)</label>
                <textarea
                  value={charForm.appearance}
                  placeholder="tall, silver hair, weathered coat, pale eyes"
                  onChange={(e) => setCharForm({ ...charForm, appearance: e.target.value })}
                />
              </div>
              <div className="field">
                <label>Style</label>
                <input value={charForm.style} onChange={(e) => setCharForm({ ...charForm, style: e.target.value })} />
              </div>
              <button className="primary" disabled={busy}>
                {busy ? "Adding…" : "Add character"}
              </button>
            </form>
          </Card>
        </div>
      )}

      {tab === "stories" && (
        <Card title="Stories">
          {stories.length === 0 ? (
            <Empty>
              No stories yet.{" "}
              <button className="sm" onClick={() => navigate(`/story?project=${projectId}`)}>
                Generate one
              </button>
            </Empty>
          ) : (
            <div className="stack">
              {stories.map((story) => (
                <div key={story.id} className="card tight" style={{ cursor: "pointer" }} onClick={() => navigate(`/story?story=${story.id}`)}>
                  <div className="spread">
                    <strong>{story.title}</strong>
                    <span className="chip-row">
                      <span className="badge">{story.engine}</span>
                      <span className="badge">{story.generated_shots}/{story.shot_count} shots generated</span>
                    </span>
                  </div>
                  <p className="muted" style={{ margin: "6px 0 0" }}>{story.logline}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {tab === "assets" && (
        <Card title={`Assets (${assets.length})`}>
          {assets.length === 0 ? (
            <Empty>No assets in this project yet.</Empty>
          ) : (
            <div className="grid assets">
              {assets.map((asset) => (
                <AssetTile key={asset.id} asset={asset} onClick={() => navigate(`/library?asset=${asset.id}`)} />
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
