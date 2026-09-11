import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatBytes, timeAgo, type Asset } from "../api";
import { AssetTile, Card, Empty, Modal, ModeBadge, Spinner, usePolling, useToast } from "../components/ui";

const EDIT_PRESETS: { label: string; ops: any[] }[] = [
  { label: "Vignette + grain", ops: [{ op: "vignette", strength: 0.4 }, { op: "grain", amount: 0.04 }] },
  { label: "Cinematic grade", ops: [{ op: "adjust", contrast: 1.12, saturation: 0.92 }, { op: "vignette", strength: 0.3 }] },
  { label: "Black & white", ops: [{ op: "grayscale" }, { op: "adjust", contrast: 1.1 }] },
  { label: "Sepia", ops: [{ op: "sepia" }] },
  { label: "Sharpen", ops: [{ op: "sharpen", percent: 160 }] },
  { label: "Posterise", ops: [{ op: "posterize", bits: 4 }] },
  { label: "Pixelate", ops: [{ op: "pixelate", factor: 16 }] },
  { label: "Edge detect", ops: [{ op: "edge" }] },
];

export default function Library() {
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<Asset[]>([]);
  const [total, setTotal] = useState(0);
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Asset | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const { push } = useToast();

  const load = useCallback(async () => {
    try {
      const data = await api.listAssets({ kind, search, limit: 120 });
      setItems(data.items);
      setTotal(data.total);
    } catch (error: any) {
      push("error", error.message);
    }
  }, [kind, search, push]);

  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, 8000);

  const focusId = params.get("asset");
  useEffect(() => {
    if (focusId) {
      api.getAsset(focusId).then((asset) => {
        setSelected(asset);
        setDetail(asset);
      }).catch(() => {});
    }
  }, [focusId]);

  const stats = useMemo(() => {
    const counts = items.reduce<Record<string, number>>((acc, asset) => {
      acc[asset.kind] = (acc[asset.kind] ?? 0) + 1;
      return acc;
    }, {});
    return counts;
  }, [items]);

  async function applyEdit(ops: any[]) {
    if (!selected) return;
    setBusy(true);
    try {
      const result = await api.editAsset(selected.id, ops, "png");
      push("ok", "Edit applied — new derived asset created");
      await load();
      if (result.asset) {
        setSelected(result.asset);
        setDetail(result.asset);
      }
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await api.uploadAsset(file);
      }
      push("ok", `${files.length} file(s) uploaded`);
      load();
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <div>
          <h1>Asset library</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            {total} assets · {Object.entries(stats).map(([k, v]) => `${v} ${k}`).join(", ") || "empty"}
          </p>
        </div>
        <div className="row">
          <input type="file" multiple style={{ width: 210 }} onChange={(e) => upload(e.target.files)} disabled={uploading} />
          {uploading && <Spinner />}
          <button onClick={() => api.rebuildThumbnail("x").catch(() => {})} style={{ display: "none" }} />
        </div>
      </div>

      <Card tight>
        <div className="row">
          <input placeholder="Search prompts and filenames…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ maxWidth: 320 }} />
          <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ maxWidth: 160 }}>
            <option value="">All kinds</option>
            <option value="image">Images</option>
            <option value="video">Videos</option>
            <option value="audio">Audio</option>
          </select>
          <div className="spacer" style={{ flex: 1 }} />
          <button className="sm" onClick={load}>
            ⟳ Refresh
          </button>
        </div>
      </Card>

      {items.length === 0 ? (
        <Card>
          <Empty>No assets match. Generate something in Image Studio or upload a file.</Empty>
        </Card>
      ) : (
        <div className="grid assets">
          {items.map((asset) => (
            <AssetTile
              key={asset.id}
              asset={asset}
              selected={selected?.id === asset.id}
              onClick={async () => {
                setSelected(asset);
                setParams({ asset: asset.id }, { replace: true });
                try {
                  setDetail(await api.getAsset(asset.id));
                } catch {
                  setDetail(asset);
                }
              }}
            />
          ))}
        </div>
      )}

      {selected && (
        <Modal
          title={selected.filename}
          onClose={() => {
            setSelected(null);
            setDetail(null);
            setParams({}, { replace: true });
          }}
        >
          <div className="grid cols-2">
            <div className="stack">
              {selected.kind === "image" && <img src={selected.url} alt="" className="player" />}
              {selected.kind === "video" && <video src={selected.url} className="player" controls autoPlay loop />}
              {selected.kind === "audio" && <audio src={selected.url} className="audio-player" controls />}
              <div className="row">
                <a className="btn sm" href={selected.download_url} download>
                  ⭳ Download
                </a>
                <button
                  className="sm"
                  onClick={async () => {
                    try {
                      await api.rebuildThumbnail(selected.id);
                      push("ok", "Thumbnail rebuilt");
                      load();
                    } catch (error: any) {
                      push("error", error.message);
                    }
                  }}
                >
                  Rebuild thumbnail
                </button>
                <button
                  className="sm danger"
                  onClick={async () => {
                    if (!confirm(`Delete ${selected.filename}?`)) return;
                    try {
                      await api.deleteAsset(selected.id);
                      push("ok", "Asset deleted");
                      setSelected(null);
                      load();
                    } catch (error: any) {
                      push("error", error.message);
                    }
                  }}
                >
                  Delete
                </button>
              </div>
            </div>

            <div className="stack">
              <Card title="Details" tight>
                <dl className="kv">
                  <dt>Kind</dt>
                  <dd>{selected.kind}</dd>
                  <dt>Engine</dt>
                  <dd>
                    <span className="mono">{selected.engine || "—"}</span>{" "}
                    {selected.meta?.quality === "demo" && <ModeBadge mode="demo" />}
                  </dd>
                  <dt>Provider</dt>
                  <dd className="mono">{selected.provider || "—"}</dd>
                  <dt>Dimensions</dt>
                  <dd>{selected.width && selected.height ? `${selected.width} × ${selected.height}` : "—"}</dd>
                  <dt>Duration</dt>
                  <dd>{selected.duration_s ? `${selected.duration_s.toFixed(2)}s` : "—"}</dd>
                  <dt>Size</dt>
                  <dd>{formatBytes(selected.size_bytes)}</dd>
                  <dt>Created</dt>
                  <dd>{timeAgo(selected.created_at)}</dd>
                  <dt>SHA-256</dt>
                  <dd className="mono truncate" title={(selected as any).sha256}>
                    {(selected as any).sha256?.slice(0, 24)}…
                  </dd>
                </dl>
                {selected.prompt && (
                  <>
                    <label style={{ marginTop: 10 }}>Prompt</label>
                    <div className="mono faint" style={{ whiteSpace: "pre-wrap" }}>{selected.prompt}</div>
                  </>
                )}
              </Card>

              {selected.kind === "image" && (
                <Card title="Edit (non-destructive)" tight>
                  <div className="chip-row">
                    {EDIT_PRESETS.map((preset) => (
                      <button key={preset.label} className="sm" disabled={busy} onClick={() => applyEdit(preset.ops)}>
                        {preset.label}
                      </button>
                    ))}
                  </div>
                  <div className="row" style={{ marginTop: 10 }}>
                    <button
                      className="sm"
                      disabled={busy}
                      onClick={() => applyEdit([{ op: "resize", width: Math.round((selected.width ?? 1024) / 2), height: Math.round((selected.height ?? 576) / 2) }])}
                    >
                      Half size
                    </button>
                    <button
                      className="sm"
                      disabled={busy}
                      onClick={() => applyEdit([{ op: "resize", width: Math.round((selected.width ?? 1024) * 2), height: Math.round((selected.height ?? 576) * 2) }])}
                    >
                      Double size
                    </button>
                    <button className="sm" disabled={busy} onClick={() => applyEdit([{ op: "rotate", degrees: 90 }])}>
                      Rotate 90°
                    </button>
                    <button className="sm" disabled={busy} onClick={() => applyEdit([{ op: "flip", axis: "horizontal" }])}>
                      Flip
                    </button>
                  </div>
                  <WatermarkBox onSubmit={(text) => applyEdit([{ op: "text", text, position: "bottom-right", size: 28, background: "#0b1220" }])} busy={busy} />
                </Card>
              )}

              {detail?.derived_assets?.length ? (
                <Card title={`Derived (${detail.derived_assets.length})`} tight>
                  <div className="grid assets">
                    {detail.derived_assets.slice(0, 6).map((asset: Asset) => (
                      <AssetTile key={asset.id} asset={asset} showMeta={false} onClick={() => { setSelected(asset); api.getAsset(asset.id).then(setDetail); }} />
                    ))}
                  </div>
                </Card>
              ) : null}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function WatermarkBox({ onSubmit, busy }: { onSubmit: (text: string) => void; busy: boolean }) {
  const [text, setText] = useState("");
  return (
    <div className="row" style={{ marginTop: 10 }}>
      <input placeholder="Watermark text…" value={text} onChange={(e) => setText(e.target.value)} style={{ maxWidth: 220 }} />
      <button className="sm primary" disabled={!text.trim() || busy} onClick={() => onSubmit(text)}>
        Apply text
      </button>
    </div>
  );
}
