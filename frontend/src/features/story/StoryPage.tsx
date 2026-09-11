import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Clapperboard, Film, Lock, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import { endpoints } from '@/lib/api';
import { Badge, Button, Card, EmptyState, Input, ProgressBar, SectionTitle, Select, Slider, TextArea } from '@/components/ui';
import { useAppStore } from '@/app/store';
import { useJobPolling } from '@/lib/hooks';

export function StoryPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const toast = useAppStore((s) => s.toast);

  const [projects, setProjects] = useState<any[]>([]);
  const [selectedProject, setSelectedProject] = useState(projectId ?? '');
  const [idea, setIdea] = useState('a 2-minute animated children’s story about a lion and a rabbit');
  const [seconds, setSeconds] = useState(60);
  const [audience, setAudience] = useState('children');
  const [language, setLanguage] = useState('en');
  const [target, setTarget] = useState<'image' | 'video'>('image');
  const [scenes, setScenes] = useState<any[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [sceneJob, setSceneJob] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);

  const { job, done } = useJobPolling(jobId, 1500);
  const scenePoll = useJobPolling(sceneJob, 1500);

  useEffect(() => {
    endpoints.projects({ limit: 100 }).then((r) => setProjects(r.items)).catch(() => setProjects([]));
  }, []);

  const loadScenes = () => {
    if (!selectedProject) return setScenes([]);
    endpoints.story(selectedProject).then((r) => setScenes(r.scenes ?? [])).catch(() => setScenes([]));
  };

  useEffect(() => { loadScenes(); }, [selectedProject]);
  useEffect(() => { setSelectedProject(projectId ?? ''); }, [projectId]);

  useEffect(() => {
    if (done && job?.status === 'completed') {
      toast({ kind: 'success', title: 'Story generated', message: 'Scenes are ready — regenerate any scene individually' });
      loadScenes();
    }
    if (done && job?.status === 'failed') {
      toast({ kind: 'error', title: 'Story failed', message: job.error?.message, action: job.error?.suggested_action });
    }
  }, [done, job]);

  useEffect(() => {
    if (scenePoll.done && scenePoll.job?.status === 'completed') {
      toast({ kind: 'success', title: 'Scene regenerated' });
      loadScenes();
    }
  }, [scenePoll.done, scenePoll.job]);

  async function generateStory() {
    if (!selectedProject) {
      const created = await endpoints.createProject({ name: 'Untitled story', kind: 'story' });
      setSelectedProject(created.id);
      navigate(`/story/${created.id}`);
    }
    try {
      const res = await endpoints.generateStory({
        prompt: idea,
        duration_sec: seconds,
        audience,
        language,
        target,
        project_id: selectedProject || undefined,
      });
      if (res.job_id) setJobId(res.job_id);
      if (res.project_id && !selectedProject) setSelectedProject(res.project_id);
      toast({ kind: 'success', title: 'Story queued', message: `${res.plan?.scene_count ?? ''} scenes planned` });
      if (!res.job_id) loadScenes();
    } catch (err) {
      toast({ kind: 'error', title: 'Could not generate story', message: (err as Error).message });
    }
  }

  async function regenerateScene(sceneId: string, sceneTarget: 'image' | 'video' = 'image') {
    try {
      const res = await endpoints.regenerateScene(sceneId, { target: sceneTarget });
      setSceneJob(res.job_id);
      toast({ kind: 'success', title: 'Scene queued' });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not regenerate', message: (err as Error).message });
    }
  }

  async function saveScene(scene: any, patch: Record<string, unknown>) {
    await endpoints.patchScene(scene.id, patch);
    setEditing(null);
    loadScenes();
    toast({ kind: 'success', title: 'Scene updated' });
  }

  async function renderStory() {
    if (!selectedProject) return;
    try {
      const res = await endpoints.renderStory(selectedProject, { preset: 'youtube', captions: true });
      toast({ kind: 'success', title: 'Story render queued', message: res.job_id.slice(0, 12) });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not render story', message: (err as Error).message });
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Story to video</h1>
        <p className="text-xs text-ink-faint">
          Idea → title → characters → script → scenes → shots → images → video. Regenerate one scene at a time.
        </p>
      </div>

      <Card className="space-y-3">
        <TextArea label="Your story idea" rows={2} value={idea} onChange={(e) => setIdea(e.target.value)} />
        <div className="grid gap-3 md:grid-cols-4">
          <Select label="Project" value={selectedProject}
            onChange={(e) => setSelectedProject(e.target.value)}
            options={[{ value: '', label: 'Create new project' }, ...projects.map((p) => ({ value: p.id, label: p.name }))]} />
          <Slider label="Duration" min={24} max={240} step={12} value={seconds} suffix="s" onChange={setSeconds} />
          <Select label="Audience" value={audience} onChange={(e) => setAudience(e.target.value)}
            options={['children', 'general', 'corporate'].map((a) => ({ value: a, label: a }))} />
          <Select label="Language" value={language} onChange={(e) => setLanguage(e.target.value)}
            options={[['en', 'English'], ['hi', 'Hindi'], ['te', 'Telugu'], ['ta', 'Tamil']].map(([v, l]) => ({ value: v, label: l }))} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={target} onChange={(e) => setTarget(e.target.value as 'image' | 'video')}
            options={[{ value: 'image', label: 'Generate storyboard images' }, { value: 'video', label: 'Generate scene videos' }]}
            className="w-64" />
          <Button variant="primary" icon={<Sparkles className="h-4 w-4" />} loading={jobId !== null && !done}
            onClick={() => void generateStory()}>Generate story</Button>
          {scenes.length > 0 && (
            <Button icon={<Film className="h-4 w-4" />} onClick={() => void renderStory()}>
              Render story video
            </Button>
          )}
        </div>
        {job && !done && (
          <div className="space-y-1">
            <ProgressBar value={job.progress} />
            <p className="text-[11px] text-ink-faint">{job.stage} · {Math.round(job.progress)}%</p>
          </div>
        )}
      </Card>

      {scenes.length === 0 ? (
        <EmptyState icon={<Clapperboard className="h-5 w-5" />} title="No scenes yet"
          description="Describe your story above and the engine will build the script, scenes, shots and prompts."
          action={<Button variant="primary" onClick={() => void generateStory()}>Generate story</Button>} />
      ) : (
        <div className="space-y-3">
          <SectionTitle title={`Storyboard · ${scenes.length} scenes`}
            subtitle="Each scene regenerates independently — nothing else is touched" />
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {scenes.map((scene) => (
              <Card key={scene.id} className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm font-medium text-ink">{scene.title}</p>
                  <div className="flex items-center gap-1">
                    {scene.locked && <Lock className="h-3 w-3 text-brand" />}
                    <Badge tone={scene.status === 'ready' ? 'success' : 'default'}>{scene.status}</Badge>
                  </div>
                </div>

                <div className="aspect-video overflow-hidden rounded-lg border border-edge bg-surface-2">
                  {scene.image_url ? (
                    <img src={scene.image_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-[11px] text-ink-faint">No image yet</div>
                  )}
                </div>

                {editing === scene.id ? (
                  <div className="space-y-2">
                    <Input label="Title" defaultValue={scene.title} id={`title-${scene.id}`} />
                    <TextArea label="Narration" rows={3} defaultValue={scene.narration} id={`narr-${scene.id}`} />
                    <TextArea label="Video prompt" rows={2} defaultValue={scene.video_prompt} id={`vp-${scene.id}`} />
                    <div className="flex gap-2">
                      <Button size="sm" variant="primary" onClick={() => void saveScene(scene, {
                        title: (document.getElementById(`title-${scene.id}`) as HTMLInputElement).value,
                        narration: (document.getElementById(`narr-${scene.id}`) as HTMLTextAreaElement).value,
                        video_prompt: (document.getElementById(`vp-${scene.id}`) as HTMLTextAreaElement).value,
                      })}>Save</Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
                    </div>
                  </div>
                ) : (
                  <p className="line-clamp-3 text-[11px] text-ink-faint">{scene.narration}</p>
                )}

                {scene.shots?.[0] && (
                  <div className="flex flex-wrap gap-1">
                    <Badge>{scene.shots[0].camera}</Badge>
                    <Badge>{scene.shots[0].lens}</Badge>
                    <Badge>{scene.shots[0].motion}</Badge>
                    <Badge>{scene.shots[0].lighting}</Badge>
                    <Badge tone="info">{scene.shots[0].duration_sec}s</Badge>
                  </div>
                )}

                <div className="flex flex-wrap gap-1.5 pt-1">
                  <Button size="sm" variant="subtle" icon={<RefreshCw className="h-3.5 w-3.5" />}
                    onClick={() => void regenerateScene(scene.id, 'image')}>Regenerate image</Button>
                  <Button size="sm" icon={<Film className="h-3.5 w-3.5" />}
                    onClick={() => void regenerateScene(scene.id, 'video')}>Scene video</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(scene.id)}>Edit</Button>
                  <Button size="sm" variant="ghost" onClick={() => void endpoints.deleteScene(scene.id).then(loadScenes)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
