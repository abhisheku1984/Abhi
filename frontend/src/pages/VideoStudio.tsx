import { useCallback, useEffect, useState } from "react";
import { api, pollJob, type Asset, type Job, type Project } from "../api";
import { AssetTile, Card, Empty, JobLog, ModeBadge, ProgressBar, Spinner, useToast } from "../components/ui";

const MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"];
const TRANSITIONS = ["fade", "cut", "dissolve", "wipe_left", "wipe_right", "slide_up", "circle", "pixelize", "fade_white"];

interface Clip {
  asset: Asset;
  duration: number;
  motion: string;
  caption: string;
  kind: "image" | "video";
}

export default function VideoStudio() {
  const [tab, setTab] = useState<"timeline" | "generate" | "lipsync">("timeline");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [audio, setAudio] = useState<Asset[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [transition, setTransition] = useState("fade");
  const [transitionDuration, setTransitionDuration] = useState(0.6);
  const [resolution, setResolution] = useState("1280x720");
  const [fps, setFps] = useState(30);
  const [narrationId, setNarrationId] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [output, setOutput] = useState<Asset | null>(null);
  const [busy, setBusy] = useState(false);
  const [capability, setCapability] = useState<any>(null);
  const { push } = useToast();

  const [genPrompt, setGenPrompt] = useState("drone shot over a stormy coastline at sunrise");
  const [genDuration, setGenDuration] = useState(5);

  const [faceId, setFaceId] = useState("");
  const [lipsyncText, setLipsyncText] = useState("Hello, and welcome to Abhi Studio.");
  const [lipsyncAudio, setLipsyncAudio] = useState("");
  const [lipsyncCap, setLipsyncCap] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      const [projectsData, images, videos, audioData] = await Promise.all([
        api.listProjects(),
        api.listAssets({ kind: "image", limit: 120 }),
        api.listAssets({ kind: "video", limit: 60 }),
        api.listAssets({ kind: "audio", limit: 60 }),
      ]);
      setProjects(projectsData.items);
      setAssets([...images.items, ...videos.items]);
      setAudio(audioData.items);
    } catch (error: any) {
      push("error", error.message);
    }
  }, [push]);

  useEffect(() => {
    load();
    api.capabilities().then((data) => {
      setCapability(data.capabilities.video);
      setLipsyncCap(data.capabilities.lipsync);
    });
  }, [load]);

  function addClip(asset: Asset) {
    setClips((current) => [
      ...current,
      { asset, duration: asset.kind === "video" ? Math.min(10, asset.duration_s ?? 5) : 3, motion: "zoom_in", caption: "", kind: asset.kind === "video" ? "video" : "image" },
    ]);
  }

  async function render() {
    if (!clips.length) return;
    setBusy(true);
    setOutput(null);
    try {
      const [width, height] = resolution.split("x").map(Number);
      const queued = await api.renderVideo({
        project_id: projectId || null,
        clips: clips.map((clip) => ({
          asset_id: clip.asset.id,
          kind: clip.kind,
          duration: clip.duration,
          motion: clip.motion,
          caption: clip.caption,
        })),
        width,
        height,
        fps,
        transition,
        transition_duration: transitionDuration,
        audio_asset_id: narrationId || null,
      });
      const finished = await pollJob(queued.job_id, setJob);
      setJob(finished);
      if (finished.status === "succeeded") {
        const produced = (finished.result?.assets as Asset[]) ?? [];
        setOutput(produced[produced.length - 1] ?? produced[0] ?? null);
        push("ok", "Render complete — real H.264 MP4 written to the library");
        load();
      } else {
        push("error", finished.error || "Render failed");
      }
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  async function generateVideo() {
    setBusy(true);
    setOutput(null);
    try {
      const queued = await api.generateVideo({ prompt: genPrompt, project_id: projectId || null, duration: genDuration });
      const finished = await pollJob(queued.job_id, setJob, { timeoutMs: 30 * 60 * 1000 });
      setJob(finished);
      if (finished.status === "succeeded") {
        const produced = (finished.result?.assets as Asset[]) ?? [];
        setOutput(produced[0] ?? null);
        push("ok", "Video model output saved");
        load();
      } else {
        push("error", finished.error || "Video generation failed");
      }
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  async function runLipsync() {
    if (!faceId) return push("error", "Select a face asset first");
    setBusy(true);
    try {
      const queued = await api.lipsync({
        asset_id: faceId,
        audio_asset_id: lipsyncAudio || null,
        text: lipsyncAudio ? null : lipsyncText,
        project_id: projectId || null,
      });
      const finished = await pollJob(queued.job_id, setJob, { timeoutMs: 45 * 60 * 1000 });
      setJob(finished);
      if (finished.status === "succeeded") {
        const produced = (finished.result?.assets as Asset[]) ?? [];
        setOutput(produced[0] ?? null);
        push("ok", "Lip sync complete");
      } else {
        push("error", finished.error || "Lip sync failed");
      }
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  const timelineDuration =
    clips.reduce((total, clip) => total + clip.duration, 0) - Math.max(0, clips.length - 1) * (transition === "cut" ? 0 : transitionDuration);

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <div>
          <h1>Video Studio</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            Timeline renderer (real FFmpeg), generative video models and lip sync.
          </p>
        </div>
        <div className="row">
          <button className={tab === "timeline" ? "primary" : ""} onClick={() => setTab("timeline")}>
            Timeline render
          </button>
          <button className={tab === "generate" ? "primary" : ""} onClick={() => setTab("generate")}>
            AI video model
          </button>
          <button className={tab === "lipsync" ? "primary" : ""} onClick={() => setTab("lipsync")}>
            Lip sync
          </button>
        </div>
      </div>

      {tab === "timeline" && (
        <div className="grid" style={{ gridTemplateColumns: "1fr minmax(300px, 360px)", alignItems: "start" }}>
          <div className="stack">
            <Card title={`Timeline (${clips.length} clips · ${timelineDuration.toFixed(1)}s)`}>
              {clips.length === 0 ? (
                <Empty>Click assets below to add clips. Motion, duration and captions are per clip.</Empty>
              ) : (
                <div className="stack">
                  {clips.map((clip, index) => (
                    <div key={`${clip.asset.id}-${index}`} className="card tight">
                      <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
                        <img
                          src={clip.asset.thumb_url || clip.asset.url}
                          alt=""
                          style={{ width: 108, height: 62, objectFit: "cover", borderRadius: 8, border: "1px solid var(--line)" }}
                        />
                        <div style={{ flex: 1, minWidth: 220 }}>
                          <div className="spread">
                            <strong className="truncate" style={{ maxWidth: 260 }}>
                              {index + 1}. {clip.asset.filename}
                            </strong>
                            <div className="row">
                              <button className="sm" disabled={index === 0} onClick={() => setClips((c) => { const n = [...c]; [n[index - 1], n[index]] = [n[index], n[index - 1]]; return n; })}>
                                ↑
                              </button>
                              <button className="sm" disabled={index === clips.length - 1} onClick={() => setClips((c) => { const n = [...c]; [n[index + 1], n[index]] = [n[index], n[index + 1]]; return n; })}>
                                ↓
                              </button>
                              <button className="sm danger" onClick={() => setClips((c) => c.filter((_, i) => i !== index))}>
                                ✕
                              </button>
                            </div>
                          </div>
                          <div className="field-row" style={{ marginTop: 8 }}>
                            <div className="field" style={{ marginBottom: 0 }}>
                              <label>Duration (s)</label>
                              <input
                                type="number"
                                step="0.5"
                                min="0.5"
                                value={clip.duration}
                                onChange={(event) =>
                                  setClips((c) => c.map((item, i) => (i === index ? { ...item, duration: Number(event.target.value) } : item)))
                                }
                              />
                            </div>
                            <div className="field" style={{ marginBottom: 0 }}>
                              <label>Motion / camera</label>
                              <select
                                value={clip.motion}
                                disabled={clip.kind === "video"}
                                onChange={(event) => setClips((c) => c.map((item, i) => (i === index ? { ...item, motion: event.target.value } : item)))}
                              >
                                {MOTIONS.map((motion) => (
                                  <option key={motion}>{motion}</option>
                                ))}
                              </select>
                            </div>
                          </div>
                          <input
                            style={{ marginTop: 8 }}
                            placeholder="Caption burned into this clip (optional)"
                            value={clip.caption}
                            onChange={(event) => setClips((c) => c.map((item, i) => (i === index ? { ...item, caption: event.target.value } : item)))}
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card title="Add clips from the library">
              {assets.length === 0 ? (
                <Empty>No image or video assets yet — generate some first.</Empty>
              ) : (
                <div className="grid assets" style={{ maxHeight: 380, overflowY: "auto" }}>
                  {assets.slice(0, 40).map((asset) => (
                    <AssetTile key={asset.id} asset={asset} onClick={() => addClip(asset)} />
                  ))}
                </div>
              )}
            </Card>
          </div>

          <Card title="Render settings">
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
            <div className="field-row">
              <div className="field">
                <label>Resolution</label>
                <select value={resolution} onChange={(event) => setResolution(event.target.value)}>
                  <option value="1280x720">1280×720</option>
                  <option value="1920x1080">1920×1080</option>
                  <option value="854x480">854×480</option>
                  <option value="1080x1920">1080×1920</option>
                </select>
              </div>
              <div className="field">
                <label>FPS</label>
                <select value={fps} onChange={(event) => setFps(Number(event.target.value))}>
                  {[24, 25, 30, 60].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="field">
              <label>Transition between clips</label>
              <select value={transition} onChange={(event) => setTransition(event.target.value)}>
                {TRANSITIONS.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Transition duration (s)</label>
              <input type="number" step="0.1" min="0.1" max="3" value={transitionDuration} onChange={(event) => setTransitionDuration(Number(event.target.value))} />
            </div>
            <div className="field">
              <label>Narration / music track (optional — muxed in)</label>
              <select value={narrationId} onChange={(event) => setNarrationId(event.target.value)}>
                <option value="">— none —</option>
                {audio.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.filename} ({asset.duration_s?.toFixed(1)}s)
                  </option>
                ))}
              </select>
            </div>
            <button className="primary" style={{ width: "100%" }} onClick={render} disabled={busy || !clips.length}>
              {busy ? <Spinner label="rendering…" /> : "▶ Render film"}
            </button>
            {job && (
              <div className="stack" style={{ marginTop: 14 }}>
                <div className="spread">
                  <span className="faint">{job.status}</span>
                  <span className="faint">{Math.round(job.progress * 100)}%</span>
                </div>
                <ProgressBar value={job.progress} />
                <JobLog job={job} />
              </div>
            )}
          </Card>
        </div>
      )}

      {tab === "generate" && (
        <div className="grid cols-2">
          <Card
            title="Generative video model"
            actions={<ModeBadge mode={capability?.mode ?? "unknown"} title={capability?.selected_label} />}
          >
            {capability?.mode === "not_configured" && (
              <div className="banner warn" style={{ marginBottom: 12 }}>
                <span>
                  <strong>No video model configured.</strong> Diffusion video needs a GPU or a paid API. Set{" "}
                  <span className="mono">REPLICATE_API_TOKEN</span>, <span className="mono">RUNWAY_API_KEY</span>, or{" "}
                  <span className="mono">ABHI_COMFYUI_URL</span> (your own GPU host running Wan/AnimateDiff). The adapter
                  is already implemented — see System &amp; Models.
                </span>
              </div>
            )}
            <div className="field">
              <label>Prompt</label>
              <textarea value={genPrompt} onChange={(event) => setGenPrompt(event.target.value)} />
            </div>
            <div className="field-row">
              <div className="field">
                <label>Duration (s)</label>
                <input type="number" min={2} max={20} value={genDuration} onChange={(event) => setGenDuration(Number(event.target.value))} />
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
            </div>
            <button className="primary" onClick={generateVideo} disabled={busy}>
              {busy ? <Spinner label="model running…" /> : "▶ Generate video"}
            </button>
            <p className="faint" style={{ marginTop: 10 }}>
              This calls a real model endpoint. It is not simulated: if no provider is configured the job fails with the
              exact configuration needed.
            </p>
          </Card>
          <Card title="Output">{output ? <video src={output.url} className="player" controls autoPlay loop /> : <Empty>No output yet.</Empty>}</Card>
        </div>
      )}

      {tab === "lipsync" && (
        <div className="grid cols-2">
          <Card title="Lip sync" actions={<ModeBadge mode={lipsyncCap?.mode ?? "unknown"} />}>
            {lipsyncCap?.mode === "not_configured" && (
              <div className="banner warn" style={{ marginBottom: 12 }}>
                <span>
                  Configure a local <span className="mono">ABHI_WAV2LIP_DIR</span> /{" "}
                  <span className="mono">ABHI_SADTALKER_DIR</span> checkout, or set{" "}
                  <span className="mono">REPLICATE_API_TOKEN</span> to use a hosted lip-sync model.
                </span>
              </div>
            )}
            <div className="field">
              <label>Face image or video</label>
              <select value={faceId} onChange={(event) => setFaceId(event.target.value)}>
                <option value="">— select —</option>
                {assets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.filename}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Audio track (optional)</label>
              <select value={lipsyncAudio} onChange={(event) => setLipsyncAudio(event.target.value)}>
                <option value="">— synthesise from text instead —</option>
                {audio.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.filename}
                  </option>
                ))}
              </select>
            </div>
            {!lipsyncAudio && (
              <div className="field">
                <label>Text to speak</label>
                <textarea value={lipsyncText} onChange={(event) => setLipsyncText(event.target.value)} />
              </div>
            )}
            <button className="primary" onClick={runLipsync} disabled={busy || !faceId}>
              {busy ? <Spinner label="running…" /> : "▶ Lip sync"}
            </button>
          </Card>
          <Card title="Output">{output ? <video src={output.url} className="player" controls /> : <Empty>No output yet.</Empty>}</Card>
        </div>
      )}

      {(tab === "generate" || tab === "lipsync") && job && (
        <Card title="Job log">
          <JobLog job={job} />
        </Card>
      )}
    </div>
  );
}
