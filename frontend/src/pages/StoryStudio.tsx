import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, pollJob, type Asset, type Job, type Project, type Storyboard } from "../api";
import { Card, Empty, JobLog, ModeBadge, ProgressBar, Spinner, useToast } from "../components/ui";

const MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "static"];
const TRANSITIONS = ["cut", "fade", "dissolve", "wipe_left"];

export default function StoryStudio() {
  const [params, setParams] = useSearchParams();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(params.get("project") ?? "");
  const [stories, setStories] = useState<any[]>([]);
  const [storyId, setStoryId] = useState(params.get("story") ?? "");
  const [board, setBoard] = useState<Storyboard | null>(null);
  const [premise, setPremise] = useState("A deep-sea cartographer intercepts a song from a trench that should be silent.");
  const [scenes, setScenes] = useState(4);
  const [shotsPerScene, setShotsPerScene] = useState(3);
  const [style, setStyle] = useState("cinematic, volumetric light");
  const [llmCap, setLlmCap] = useState<any>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [output, setOutput] = useState<Asset | null>(null);
  const [narration, setNarration] = useState(false);
  const { push } = useToast();

  useEffect(() => {
    api.listProjects().then((data) => {
      setProjects(data.items);
      if (!projectId && data.items.length) setProjectId(data.items[0].id);
    });
    api.capabilities().then((data) => setLlmCap(data.capabilities.llm));
  }, []);

  const loadStories = useCallback(async () => {
    if (!projectId) return;
    try {
      const data = await api.listStories(projectId);
      setStories(data.items);
      if (!storyId && data.items.length) setStoryId(data.items[0].id);
    } catch {
      setStories([]);
    }
  }, [projectId, storyId]);

  useEffect(() => {
    loadStories();
  }, [loadStories]);

  const loadBoard = useCallback(async () => {
    if (!storyId) return setBoard(null);
    try {
      setBoard(await api.storyboard(storyId));
    } catch {
      setBoard(null);
    }
  }, [storyId]);
  useEffect(() => {
    loadBoard();
  }, [loadBoard]);

  async function generateStory() {
    if (!premise.trim()) return;
    setBusy(true);
    try {
      const queued = await api.generateStory({
        premise,
        project_id: projectId || null,
        style,
        scenes,
        shots_per_scene: shotsPerScene,
      });
      const finished = await pollJob(queued.job_id, setJob);
      setJob(finished);
      if (finished.status === "succeeded") {
        const newStoryId = finished.result.result?.story_id;
        push("ok", `Story written with ${finished.result.result?.shots ?? 0} shots`);
        await loadStories();
        if (newStoryId) {
          setStoryId(newStoryId);
          setParams({ story: newStoryId, project: projectId });
        }
      } else {
        push("error", finished.error || "Story generation failed");
      }
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  async function generateAll() {
    if (!storyId) return;
    setBusy(true);
    try {
      const queued = await api.generateStoryShots(storyId, { only_missing: true, narration });
      push("info", `${queued.count} job(s) queued`);
      loadBoard();
      const timer = setInterval(loadBoard, 2500);
      setTimeout(() => clearInterval(timer), 120000);
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  async function renderFilm() {
    if (!storyId) return;
    setBusy(true);
    setOutput(null);
    try {
      const queued = await api.renderStory(storyId, {}, false);
      const finished = await pollJob(queued.job_id, setJob, { timeoutMs: 30 * 60 * 1000 });
      setJob(finished);
      if (finished.status === "succeeded") {
        const produced = (finished.result?.assets as Asset[]) ?? [];
        setOutput(produced[produced.length - 1] ?? produced[0] ?? null);
        push("ok", "Film rendered from the storyboard");
      } else {
        push("error", finished.error || "Render failed");
      }
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  async function updateShot(shotId: string, patch: Record<string, any>) {
    try {
      await api.updateShot(shotId, patch);
      loadBoard();
    } catch (error: any) {
      push("error", error.message);
    }
  }

  async function generateShotImage(shotId: string) {
    try {
      push("info", "Generating shot image…");
      await api.generateShot(shotId, true);
      loadBoard();
    } catch (error: any) {
      push("error", error.message);
    }
  }

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <div>
          <h1>Story Studio</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            Premise → filmscript → shot list → storyboard → rendered film.
          </p>
        </div>
        <ModeBadge mode={llmCap?.mode ?? "unknown"} title={llmCap?.selected_label} />
      </div>

      {llmCap?.mode === "demo" && (
        <div className="banner warn">
          <span>
            <strong>Story structuring runs on a DEMO engine</strong> (deterministic beat-sheet template, not an LLM).
            Set <span className="mono">OPENAI_API_KEY</span>, <span className="mono">ANTHROPIC_API_KEY</span> or{" "}
            <span className="mono">ABHI_OLLAMA_URL</span> for model-written prose.
          </span>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "minmax(320px, 380px) 1fr", alignItems: "start" }}>
        <div className="stack">
          <Card title="New story">
            <div className="field">
              <label>Premise</label>
              <textarea value={premise} onChange={(event) => setPremise(event.target.value)} rows={3} />
            </div>
            <div className="field-row">
              <div className="field">
                <label>Scenes</label>
                <input type="number" min={1} max={10} value={scenes} onChange={(event) => setScenes(Number(event.target.value))} />
              </div>
              <div className="field">
                <label>Shots / scene</label>
                <input type="number" min={1} max={6} value={shotsPerScene} onChange={(event) => setShotsPerScene(Number(event.target.value))} />
              </div>
            </div>
            <div className="field">
              <label>Visual style</label>
              <input value={style} onChange={(event) => setStyle(event.target.value)} />
            </div>
            <div className="field">
              <label>Project</label>
              <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                <option value="">— none —</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </div>
            <button className="primary" style={{ width: "100%" }} onClick={generateStory} disabled={busy}>
              {busy ? <Spinner label="writing…" /> : "✎ Generate story & shot list"}
            </button>
          </Card>

          <Card title={`Stories (${stories.length})`}>
            {stories.length === 0 ? (
              <Empty>No stories in this project yet.</Empty>
            ) : (
              <div className="stack">
                {stories.map((story) => (
                  <button
                    key={story.id}
                    className={storyId === story.id ? "primary" : ""}
                    style={{ justifyContent: "space-between", textAlign: "left" }}
                    onClick={() => {
                      setStoryId(story.id);
                      setParams({ story: story.id, project: projectId });
                    }}
                  >
                    <span className="truncate" style={{ maxWidth: 220 }}>
                      {story.title}
                    </span>
                    <span className="faint">
                      {story.generated_shots}/{story.shot_count}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Card>

          {job && (
            <Card title="Current job">
              <ProgressBar value={job.progress} />
              <div className="faint" style={{ margin: "6px 0" }}>
                {job.label} · {job.status}
              </div>
              <JobLog job={job} />
            </Card>
          )}
        </div>

        <div className="stack">
          {board ? (
            <>
              <Card
                title={board.story.title}
                actions={
                  <div className="row">
                    <label className="checkbox faint" style={{ marginBottom: 0 }}>
                      <input type="checkbox" checked={narration} onChange={(event) => setNarration(event.target.checked)} /> narration
                    </label>
                    <button className="sm" onClick={generateAll} disabled={busy}>
                      ✦ Generate all images
                    </button>
                    <button className="sm primary" onClick={renderFilm} disabled={busy}>
                      ▶ Render film
                    </button>
                  </div>
                }
              >
                <p className="muted">{board.story.logline}</p>
                <div className="row">
                  <span className="badge">{board.stats.shot_count} shots</span>
                  <span className="badge">{board.stats.with_images} with images</span>
                  <span className="badge">{board.stats.with_audio} narrated</span>
                  <span className="badge">{board.stats.total_duration_s}s total</span>
                  <span className="badge">{board.story.engine}</span>
                </div>
              </Card>

              {output && (
                <Card title="Rendered film">
                  <video src={output.url} className="player" controls autoPlay loop />
                  <p className="faint" style={{ marginTop: 8 }}>
                    {output.engine} · {output.duration_s?.toFixed(1)}s
                  </p>
                </Card>
              )}

              <div className="grid cols-3">
                {board.shots.map((shot) => (
                  <div key={shot.id} className="shot-card">
                    {shot.image ? (
                      <img className="asset-thumb" src={shot.image.thumb_url || shot.image.url} alt="" style={{ aspectRatio: "16/9", objectFit: "cover", width: "100%" }} />
                    ) : (
                      <div className="asset-thumb center" style={{ aspectRatio: "16/9" }}>
                        <span className="faint">no image yet</span>
                      </div>
                    )}
                    <div className="shot-body">
                      <div className="spread">
                        <span className="faint">
                          scene {shot.scene_no} · shot {shot.index_no + 1}
                        </span>
                        <span className={`badge ${shot.image ? "succeeded" : "queued"}`}>{shot.image ? "generated" : "draft"}</span>
                      </div>
                      <input value={shot.title} onChange={(event) => setBoard((b) => (b ? { ...b, shots: b.shots.map((item) => (item.id === shot.id ? { ...item, title: event.target.value } : item)) } : b))} onBlur={(event) => updateShot(shot.id, { title: event.target.value })} />
                      <textarea value={shot.prompt} rows={3} onChange={(event) => setBoard((b) => (b ? { ...b, shots: b.shots.map((item) => (item.id === shot.id ? { ...item, prompt: event.target.value } : item)) } : b))} onBlur={(event) => updateShot(shot.id, { prompt: event.target.value })} />
                      <div className="field-row">
                        <div>
                          <label>Duration</label>
                          <input type="number" step="0.5" min="0.5" value={shot.duration_s} onChange={(event) => updateShot(shot.id, { duration_s: Number(event.target.value) })} />
                        </div>
                        <div>
                          <label>Motion</label>
                          <select value={shot.motion} onChange={(event) => updateShot(shot.id, { motion: event.target.value })}>
                            {MOTIONS.map((motion) => (
                              <option key={motion}>{motion}</option>
                            ))}
                          </select>
                        </div>
                      </div>
                      <div className="field-row">
                        <div>
                          <label>Transition</label>
                          <select value={shot.transition} onChange={(event) => updateShot(shot.id, { transition: event.target.value })}>
                            {TRANSITIONS.map((transition) => (
                              <option key={transition}>{transition}</option>
                            ))}
                          </select>
                        </div>
                        <div>
                          <label>Caption / narration</label>
                          <input value={shot.caption} onChange={(event) => setBoard((b) => (b ? { ...b, shots: b.shots.map((item) => (item.id === shot.id ? { ...item, caption: event.target.value } : item)) } : b))} onBlur={(event) => updateShot(shot.id, { caption: event.target.value })} />
                        </div>
                      </div>
                      <div className="row">
                        <button className="sm" onClick={() => generateShotImage(shot.id)}>
                          ✦ {shot.image ? "Regenerate" : "Generate"}
                        </button>
                        <button
                          className="sm"
                          onClick={async () => {
                            try {
                              await api.shotVoice(shot.id, "aria");
                              push("ok", "Narration queued for this shot");
                            } catch (error: any) {
                              push("error", error.message);
                            }
                          }}
                        >
                          ♪ Narrate
                        </button>
                        <button
                          className="sm danger"
                          onClick={async () => {
                            await api.deleteShot(shot.id);
                            loadBoard();
                          }}
                        >
                          ✕
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <Card>
              <Empty>Generate a story (or pick one) to see its storyboard here.</Empty>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
