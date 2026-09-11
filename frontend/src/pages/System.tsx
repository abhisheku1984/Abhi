import { useCallback, useEffect, useState } from "react";
import { api, formatBytes, type SystemStatus } from "../api";
import { Card, Empty, ModeBadge, Spinner, modeExplanation, useToast } from "../components/ui";

export default function SystemPage({ status, user }: { status: SystemStatus | null; user: any }) {
  const [capabilities, setCapabilities] = useState<Record<string, any>>({});
  const [legend, setLegend] = useState<Record<string, string>>({});
  const [keys, setKeys] = useState<any[]>([]);
  const [newKey, setNewKey] = useState<{ key: string; name: string } | null>(null);
  const [keyName, setKeyName] = useState("automation");
  const [passwords, setPasswords] = useState({ current: "", next: "" });
  const [busy, setBusy] = useState(false);
  const { push } = useToast();

  const load = useCallback(async () => {
    try {
      const [caps, keyList] = await Promise.all([api.capabilities(), api.listKeys()]);
      setCapabilities(caps.capabilities);
      setLegend(caps.legend);
      setKeys(keyList.items);
    } catch (error: any) {
      push("error", error.message);
    }
  }, [push]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div>
        <h1>System &amp; models</h1>
        <p className="muted" style={{ marginBottom: 0 }}>
          What actually works, what is a stand-in, and exactly what to configure to unlock the rest.
        </p>
      </div>

      <Card title="Capability matrix">
        <div className="grid cols-4" style={{ marginBottom: 14 }}>
          {Object.entries(legend).map(([mode, text]) => (
            <div key={mode} className="stack" style={{ gap: 6 }}>
              <ModeBadge mode={mode} />
              <span className="faint">{text}</span>
            </div>
          ))}
        </div>
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Capability</th>
                <th>Mode</th>
                <th>Active provider</th>
                <th>Alternatives (and what unlocks them)</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(capabilities).map(([capability, entry]: [string, any]) => (
                <tr key={capability}>
                  <td>
                    <strong style={{ textTransform: "capitalize" }}>{capability.replace("_", " ")}</strong>
                    {entry.selectable ? <div className="faint">selectable in studios</div> : <div className="faint">always local</div>}
                  </td>
                  <td>
                    <ModeBadge mode={entry.mode} title={modeExplanation(entry.mode)} />
                  </td>
                  <td>
                    {entry.selected_label}
                    <div className="faint">{entry.providers.find((p: any) => p.id === entry.selected)?.describes}</div>
                  </td>
                  <td>
                    <div className="stack">
                      {entry.providers
                        .filter((provider: any) => provider.id !== entry.selected)
                        .map((provider: any) => (
                          <div key={provider.id} className="spread" style={{ gap: 8 }}>
                            <span>
                              {provider.label}{" "}
                              <span className="faint">
                                ({provider.kind}, {provider.quality})
                              </span>
                            </span>
                            {provider.available ? (
                              <span className="badge real">ready</span>
                            ) : (
                              <span className="badge not_configured" title={provider.reason}>
                                {provider.requires.join(", ") || provider.reason}
                              </span>
                            )}
                          </div>
                        ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid cols-2">
        <Card title="Environment">
          <dl className="kv">
            <dt>Version</dt>
            <dd>{status?.app.version}</dd>
            <dt>Uptime</dt>
            <dd>{status ? `${Math.round(status.app.uptime_s / 60)} min` : "—"}</dd>
            <dt>CPU</dt>
            <dd>
              {status?.cpu.cores} cores · {status?.cpu.platform}
            </dd>
            <dt>Memory</dt>
            <dd>
              {status?.memory.available_gb} GB free / {status?.memory.total_gb} GB
            </dd>
            <dt>GPU</dt>
            <dd>{status?.gpu.available ? status.gpu.devices.map((d: any) => `${d.name} (${d.vram_mb} MB)`).join(", ") : status?.gpu.reason}</dd>
            <dt>FFmpeg</dt>
            <dd className="mono">{status?.ffmpeg.version ?? "unavailable"}</dd>
            <dt>Storage free</dt>
            <dd>{status?.storage.disk_free_gb} GB</dd>
            <dt>Media store</dt>
            <dd>{status?.storage.media_store_mb} MB</dd>
            <dt>Data dir</dt>
            <dd className="mono">{status?.storage.data_dir}</dd>
            <dt>Packages</dt>
            <dd className="mono">
              {Object.entries(status?.python_packages ?? {}).map(([name, version]) => `${name} ${version ?? "—"}`).join(" · ")}
            </dd>
          </dl>
        </Card>

        <div className="stack">
          <Card title="Job kinds available to workflows">
            <div className="chip-row">
              {(status?.job_kinds ?? []).map((kind) => (
                <span key={kind} className="badge mono">
                  {kind}
                </span>
              ))}
              {!status?.job_kinds?.length && <Empty>No handlers registered.</Empty>}
            </div>
          </Card>

          <Card title="Account">
            <div className="kv">
              <dt>User</dt>
              <dd>
                {user?.username} ({user?.role})
              </dd>
            </div>
            <div className="field" style={{ marginTop: 12 }}>
              <label>Current password</label>
              <input type="password" value={passwords.current} onChange={(event) => setPasswords({ ...passwords, current: event.target.value })} />
            </div>
            <div className="field">
              <label>New password (min 6 chars)</label>
              <input type="password" value={passwords.next} onChange={(event) => setPasswords({ ...passwords, next: event.target.value })} />
            </div>
            <button
              disabled={busy || passwords.next.length < 6}
              onClick={async () => {
                setBusy(true);
                try {
                  await api.changePassword(passwords.current, passwords.next);
                  push("ok", "Password changed");
                  setPasswords({ current: "", next: "" });
                } catch (error: any) {
                  push("error", error.message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? <Spinner /> : "Change password"}
            </button>
          </Card>
        </div>
      </div>

      <Card title="API keys (for scripts and external tools)">
        <div className="row">
          <input value={keyName} onChange={(event) => setKeyName(event.target.value)} style={{ maxWidth: 240 }} placeholder="key name" />
          <button
            className="primary"
            onClick={async () => {
              try {
                const created = await api.createKey(keyName);
                setNewKey({ key: created.key, name: created.name });
                push("ok", "API key created — copy it now, it is not shown again");
                load();
              } catch (error: any) {
                push("error", error.message);
              }
            }}
          >
            + Create key
          </button>
        </div>
        {newKey && (
          <div className="banner ok" style={{ marginTop: 12 }}>
            <div className="stack">
              <strong>Copy this key now</strong>
              <code className="mono" style={{ wordBreak: "break-all" }}>
                {newKey.key}
              </code>
              <span className="faint">
                Use it as <span className="mono">X-API-Key: &lt;key&gt;</span> for scripted access.
              </span>
            </div>
          </div>
        )}
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Prefix</th>
              <th>Created</th>
              <th>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key.id}>
                <td>{key.name}</td>
                <td className="mono">{key.prefix}…</td>
                <td className="faint">{key.created_at}</td>
                <td className="faint">{key.last_used_at ?? "never"}</td>
                <td>
                  <button
                    className="sm danger"
                    onClick={async () => {
                      await api.deleteKey(key.id);
                      push("ok", "Key revoked");
                      load();
                    }}
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
            {!keys.length && (
              <tr>
                <td colSpan={5}>
                  <Empty>No API keys yet.</Empty>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      <Card title="How to unlock model-grade generation">
        <div className="stack">
          <p className="muted" style={{ marginBottom: 4 }}>
            Every adapter is already implemented. Copy <span className="mono">.env.example</span> to{" "}
            <span className="mono">.env</span> and fill in any subset — the app reports exactly what each one unlocks.
          </p>
          <table>
            <thead>
              <tr>
                <th>Goal</th>
                <th>Set</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Photoreal images</td>
                <td className="mono">OPENAI_API_KEY · STABILITY_API_KEY · REPLICATE_API_TOKEN</td>
              </tr>
              <tr>
                <td>Your own GPU (SDXL/FLUX, Wan video)</td>
                <td className="mono">ABHI_COMFYUI_URL · ABHI_A1111_URL</td>
              </tr>
              <tr>
                <td>Neural narration</td>
                <td className="mono">ELEVENLABS_API_KEY · OPENAI_API_KEY</td>
              </tr>
              <tr>
                <td>LLM story writing</td>
                <td className="mono">OPENAI_API_KEY · ANTHROPIC_API_KEY · ABHI_OLLAMA_URL</td>
              </tr>
              <tr>
                <td>Lip sync</td>
                <td className="mono">ABHI_WAV2LIP_DIR · ABHI_SADTALKER_DIR · REPLICATE_API_TOKEN</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
