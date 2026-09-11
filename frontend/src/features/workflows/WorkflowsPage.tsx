import { useCallback, useEffect, useMemo, useState } from 'react';
import ReactFlow, { Background, Controls, addEdge, useEdgesState, useNodesState, Handle, Position } from 'reactflow';
import type { Connection, Edge, Node, NodeProps } from 'reactflow';
import 'reactflow/dist/style.css';
import { Play, Plus, Save, Workflow as WorkflowIcon } from 'lucide-react';
import { endpoints } from '@/lib/api';
import { Badge, Button, Card, EmptyState, Input, Modal, SectionTitle } from '@/components/ui';
import { useAppStore } from '@/app/store';
import { useJobPolling } from '@/lib/hooks';

interface NodeData { label: string; type: string; config: Record<string, any> }

function PipelineNode({ data, selected }: NodeProps<NodeData>) {
  return (
    <div className={`min-w-[170px] rounded-xl border px-3 py-2 text-xs shadow-lg backdrop-blur ${
      selected ? 'border-brand bg-brand/20' : 'border-edge bg-surface-2/90'}`}>
      <p className="font-medium text-ink">{data.label}</p>
      <p className="text-[10px] text-ink-faint">{data.type}</p>
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !bg-brand" />
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !bg-brand" />
    </div>
  );
}

const nodeTypes = { pipeline: PipelineNode };

function graphNodeCount(graph: any): number {
  const nodes = graph?.nodes;
  if (!nodes) return 0;
  return Array.isArray(nodes) ? nodes.length : Object.keys(nodes).length;
}

const PALETTE: { type: string; label: string; category: string }[] = [
  { type: 'input.text', label: 'Text input', category: 'INPUT' },
  { type: 'input.asset', label: 'Asset input', category: 'INPUT' },
  { type: 'ai.enhance-prompt', label: 'Enhance prompt', category: 'AI' },
  { type: 'image.text-to-image', label: 'Text → image', category: 'IMAGE' },
  { type: 'image.upscale', label: 'Upscale', category: 'IMAGE' },
  { type: 'video.image-to-video', label: 'Image → video', category: 'VIDEO' },
  { type: 'voice.tts', label: 'Text → speech', category: 'AUDIO' },
  { type: 'audio.music', label: 'Music', category: 'AUDIO' },
  { type: 'avatar.talking', label: 'Talking avatar', category: 'AVATAR' },
  { type: 'lipsync.apply', label: 'Lip sync', category: 'AVATAR' },
  { type: 'output.asset', label: 'Save asset', category: 'OUTPUT' },
];

export function WorkflowsPage() {
  const toast = useAppStore((s) => s.toast);
  const [nodes, setNodes, onNodesChange] = useNodesState<NodeData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [saved, setSaved] = useState<any[]>([]);
  const [name, setName] = useState('My workflow');
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, done } = useJobPolling(jobId, 1500);

  const onConnect = useCallback((connection: Connection) => setEdges((eds) => addEdge(connection, eds)), [setEdges]);

  useEffect(() => {
    endpoints.workflows().then((r) => setSaved(r.items ?? [])).catch(() => setSaved([]));
  }, []);

  useEffect(() => {
    if (done && job) {
      if (job.status === 'completed') toast({ kind: 'success', title: 'Workflow finished',
        message: `${((job.result?.asset_ids as unknown[]) ?? []).length} asset(s) produced` });
      else toast({ kind: 'error', title: 'Workflow failed', message: job.error?.message });
    }
  }, [done, job]);

  function addNode(item: { type: string; label: string }) {
    const id = `${item.type}-${Date.now()}`;
    setNodes((nds) => [...nds, {
      id,
      type: 'pipeline',
      position: { x: 80 + nds.length * 40, y: 60 + (nds.length % 4) * 110 },
      data: { label: item.label, type: item.type, config: {} },
    }]);
  }

  async function save() {
    setSaving(true);
    try {
      const graph = {
        nodes: nodes.map((n) => ({ id: n.id, type: n.data.type, data: n.data.config ?? {}, position: n.position })),
        edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
      };
      const result = currentId
        ? await endpoints.updateWorkflow(currentId, { name, graph })
        : await endpoints.createWorkflow({ name, graph });
      setCurrentId(result.id);
      toast({ kind: 'success', title: 'Workflow saved' });
      endpoints.workflows().then((r) => setSaved(r.items ?? []));
    } catch (err) {
      toast({ kind: 'error', title: 'Could not save workflow', message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  }

  async function run() {
    if (!nodes.length) {
      toast({ kind: 'error', title: 'Nothing to run', message: 'Add at least one node.' });
      return;
    }
    try {
      if (!currentId) await save();
      const graph = {
        nodes: nodes.map((n) => ({ id: n.id, type: n.data.type, data: n.data.config ?? {}, position: n.position })),
        edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
      };
      const res = await endpoints.runWorkflow(currentId!, graph);
      setJobId(res.job_id);
      toast({ kind: 'success', title: 'Workflow queued', message: `${nodes.length} nodes` });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not run workflow', message: (err as Error).message });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Workflow automation</h1>
          <p className="text-xs text-ink-faint">
            Connect nodes into a pipeline: prompt → image → video → voice → lip sync → render.
          </p>
        </div>
        <div className="flex gap-2">
          <Input value={name} onChange={(e) => setName(e.target.value)} className="w-48" />
          <Button icon={<Save className="h-4 w-4" />} loading={saving} onClick={() => void save()}>Save</Button>
          <Button variant="primary" icon={<Play className="h-4 w-4" />} loading={jobId !== null && !done}
            onClick={() => void run()}>Run</Button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[200px_minmax(0,1fr)]">
        <Card className="space-y-1">
          <p className="label mb-2">Node palette</p>
          {['INPUT', 'AI', 'IMAGE', 'VIDEO', 'AUDIO', 'AVATAR', 'OUTPUT'].map((category) => (
            <div key={category} className="mb-2">
              <p className="text-[9px] uppercase tracking-wider text-ink-faint">{category}</p>
              {PALETTE.filter((p) => p.category === category).map((item) => (
                <button key={item.type} onClick={() => addNode(item)}
                  className="mt-1 flex w-full items-center gap-1.5 rounded-lg border border-edge bg-surface-2/40 px-2 py-1.5 text-left text-[11px] text-ink-dim transition hover:border-brand/50 hover:text-ink">
                  <Plus className="h-3 w-3" /> {item.label}
                </button>
              ))}
            </div>
          ))}
        </Card>

        <Card className="h-[520px] p-0">
          {nodes.length === 0 ? (
            <div className="flex h-full items-center justify-center">
              <EmptyState icon={<WorkflowIcon className="h-5 w-5" />} title="Empty canvas"
                description="Add nodes from the palette and connect them to build a pipeline." />
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              nodeTypes={nodeTypes}
              fitView
            >
              <Background gap={18} color="rgb(var(--edge))" />
              <Controls />
            </ReactFlow>
          )}
        </Card>
      </div>

      {saved.length > 0 && (
        <section>
          <SectionTitle title="Saved workflows" />
          <div className="grid gap-2 md:grid-cols-3">
            {saved.map((wf) => (
              <Card key={wf.id} hover className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-xs text-ink">{wf.name}</p>
                  <p className="text-[10px] text-ink-faint">
                    {graphNodeCount(wf.graph)} nodes
                  </p>
                </div>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => {
                    setCurrentId(wf.id);
                    setName(wf.name);
                    setNodes((wf.graph?.nodes ?? []).map((n: any) => ({
                      id: n.id, type: 'pipeline', position: n.position ?? { x: 80, y: 80 },
                      data: { label: n.type, type: n.type, config: n.data ?? {} },
                    })));
                    setEdges((wf.graph?.edges ?? []).map((e: any) => ({
                      id: e.id ?? `${e.source}-${e.target}`, source: e.source, target: e.target,
                    })));
                  }}>Load</Button>
                  <Button size="sm" variant="ghost" onClick={() => void endpoints.runWorkflow(wf.id)
                    .then((r) => { setJobId(r.job_id); toast({ kind: 'success', title: 'Workflow queued' }); })
                    .catch((err: Error) => toast({ kind: 'error', title: 'Run failed', message: err.message }))}>
                    <Play className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}

      {job && (
        <Card>
          <div className="flex items-center justify-between">
            <p className="text-xs text-ink-dim">Last run: {job.status} · {job.stage}</p>
            <Badge tone={job.status === 'completed' ? 'success' : job.status === 'failed' ? 'danger' : 'brand'}>
              {Math.round(job.progress)}%
            </Badge>
          </div>
        </Card>
      )}
    </div>
  );
}
