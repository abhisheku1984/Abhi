import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, pollJob, type Asset, type Character, type Job, type Project } from "../api";
import { AssetTile, Card, Empty, JobLog, ModeBadge, ProgressBar, Spinner, useToast } from "../components/ui";

const STYLES = ["auto", "cinematic", "noir", "neon", "pastel", "vintage"];
const SIZES: { label: string; width: number; height: number }[] = [
  { label: "16:9 — 1280×720", width: 1280, height: 720 },
  { label: "16:9 — 1920×1080", width: 1920, height: 1080 },
  { label: "1:1 — 1024×1024", width: 1024, height: 1024 },
  { label: "9:16 — 720×1280", width: 720, height: 1280 },
  { label: "4:5 — 864×1080", width: 864, height: 1080 },
];

export default function ImageStudio() {
  const [params] = useSearchParams();
  const [projects, setProjects] = useState<Project[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [projectId, setProjectId] = useState(params.get("project") ?? "");
  const [prompt, setPrompt] = useState("a lone lighthouse on a storm-battered cliff at dusk, volumetric light, cinematic");
  const [style, setStyle] = useState("cinematic");
  const [sizeIndex, setSizeIndex] = useState(0);
  const [seed, setSeed] = useState<string>("");
  const [count, setCount] = useState(1);
  const [selectedCharacters, setSelectedCharacters] = useState<string[]>([]);
  const [provider, setProvider] = useState("auto");
  const [capability, setCapability] = useState<any>(null);
  const [results, setResults] = useState<Asset[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const { push } = useToast();

  useEffect(() => {
    api.listProjects().then((data) => {
      setProjects(data.items);
      if (!projectId && data.items.length) setProjectId(data.items[0].id);
    });
    api.capabilities().then((data) => setCapability(data.capabilities.image));
  }, []);

  const loadCharacters = useCallback(async () => {
    if (!projectId) return setCharacters([]);
    try {
      const data = await api.listCharacters(projectId);
      setCharacters(data.items);
    } catch {
      setCharacters([]);
    }
  }, [projectId]);
  useEffect(() => {
    loadCharacters();
  }, [loadCharacters]);

  async function generate() {
    if (!prompt.trim()) return;
    setBusy(true);
    setJob(null);
    try {
      const size = SIZES[sizeIndex];
      const queued = await api.generateImage({
        prompt,
        project_id: projectId || null,
        width: size.width,
        height: size.height,
        style,
        seed: seed ? Number(seed) : null,
        character_ids: selectedCharacters,
        provider,
        count,
      });
      const ids: string[] = queued.job_ids ?? [queued.job_id];
      push("info", `${ids.length} image job(s) queued`);
      let produced: Asset[] = [];
      for (const id of ids) {
        const finished = await pollJob(id, (tick) => setJob(tick));
        setJob(finished);
        if (finished.status === "succeeded") {
          produced = [...produced, ...((finished.result?.assets as Asset[]) ?? [])];
        } else if (finished.status === "failed") {
          push("error", finished.error || "Generation failed");
        }
      }
      setResults((current) => [...produced, ...current]);
      if (produced.length) push("ok", `${produced.length} image(s) saved to the library`);
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  const mode: string = capability?.mode ?? "unknown";

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <div>
          <h1>Image Studio</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            Prompt-driven image generation and non-destructive editing.
          </p>
        </div>
        <div className="row">
          <ModeBadge mode={mode} title={capability?.selected_label} />
          <span className="faint">{capability?.selected_label}</span>
        </div>
      </div>

      {mode === "demo" && (
        <div className="banner warn">
          <span>
            <strong>This is a DEMO engine, not a neural model.</strong> Images are produced by a seeded procedural
            generator (real deterministic artwork). Configure an OpenAI / Stability / Replicate key, or point{" "}
            <span className="mono">ABHI_COMFYUI_URL</span> or <span className="mono">ABHI_A1111_URL</span> at your GPU
            machine, to generate model-grade images.
          </span>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "minmax(320px, 400px) 1fr", alignItems: "start" }}>
        <Card title="Prompt">
          <div className="field">
            <label>Describe the image</label>
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={4} />
          </div>

          <div className="field-row">
            <div className="field">
              <label>Style</label>
              <select value={style} onChange={(event) => setStyle(event.target.value)}>
                {STYLES.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Size</label>
              <select value={sizeIndex} onChange={(event) => setSizeIndex(Number(event.target.value))}>
                {SIZES.map((option, index) => (
                  <option key={option.label} value={index}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>Seed (blank = locked per project)</label>
              <input value={seed} onChange={(event) => setSeed(event.target.value)} placeholder="random" inputMode="numeric" />
            </div>
            <div className="field">
              <label>Count (1–8)</label>
              <input type="number" min={1} max={8} value={count} onChange={(event) => setCount(Number(event.target.value))} />
            </div>
          </div>

          <div className="field">
            <label>Project (applies its style prompt)</label>
            <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              <option value="">— none —</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </div>

          {characters.length > 0 && (
            <div className="field">
              <label>Character lock (keeps appearance consistent)</label>
              <div className="chip-row">
                {characters.map((character) => {
                  const active = selectedCharacters.includes(character.id);
                  return (
                    <button
                      key={character.id}
                      className={`sm${active ? " primary" : ""}`}
                      onClick={() =>
                        setSelectedCharacters((current) =>
                          active ? current.filter((id) => id !== character.id) : [...current, character.id],
                        )
                      }
                    >
                      {character.name}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <div className="field">
            <label>Provider</label>
            <select value={provider} onChange={(event) => setProvider(event.target.value)}>
              <option value="auto">auto (best available)</option>
              {(capability?.providers ?? []).map((option: any) => (
                <option key={option.id} value={option.id} disabled={!option.available}>
                  {option.label} {option.available ? `(${option.quality})` : "— not configured"}
                </option>
              ))}
            </select>
          </div>

          <button className="primary" style={{ width: "100%" }} onClick={generate} disabled={busy || !prompt.trim()}>
            {busy ? <Spinner label="generating…" /> : "✦ Generate"}
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

        <div className="stack">
          {results.length === 0 ? (
            <Card>
              <Empty>Generated images appear here and are saved to the asset library automatically.</Empty>
            </Card>
          ) : (
            <Card title={`Session results (${results.length})`}>
              <div className="grid assets">
                {results.map((asset) => (
                  <AssetTile key={asset.id} asset={asset} />
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
