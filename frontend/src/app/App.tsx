import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from '@/components/layout/AppShell';
import { LoginPage } from '@/features/auth/LoginPage';
import { DashboardPage } from '@/features/dashboard/DashboardPage';
import { CreatePage } from '@/features/create/CreatePage';
import { ProjectsPage } from '@/features/projects/ProjectsPage';
import { ProjectDetailPage } from '@/features/projects/ProjectDetailPage';
import { AssetsPage } from '@/features/assets/AssetsPage';
import { CharactersPage } from '@/features/characters/CharactersPage';
import { VoicesPage } from '@/features/voices/VoicesPage';
import { AvatarsPage } from '@/features/avatars/AvatarsPage';
import { AudioPage } from '@/features/audio/AudioPage';
import { StoryPage } from '@/features/story/StoryPage';
import { EditorPage } from '@/features/editor/EditorPage';
import { WorkflowsPage } from '@/features/workflows/WorkflowsPage';
import { JobsPage } from '@/features/jobs/JobsPage';
import { ModelsPage } from '@/features/models/ModelsPage';
import { AdminPage } from '@/features/admin/AdminPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import { useAppStore, bootstrapBranding } from './store';
import { endpoints } from '@/lib/api';

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 10_000, retry: 1 } },
});

function RequireAuth({ children }: { children: React.ReactNode }) {
  const user = useAppStore((s) => s.user);
  const location = useLocation();
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export default function App() {
  const setUser = useAppStore((s) => s.setUser);
  const user = useAppStore((s) => s.user);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    void bootstrapBranding();
    endpoints
      .me()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, [setUser]);

  if (!ready) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand border-t-transparent" />
      </div>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <LoginPage />} />
        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/create" element={<CreatePage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/assets" element={<AssetsPage />} />
          <Route path="/characters" element={<CharactersPage />} />
          <Route path="/voices" element={<VoicesPage />} />
          <Route path="/avatars" element={<AvatarsPage />} />
          <Route path="/audio" element={<AudioPage />} />
          <Route path="/story" element={<StoryPage />} />
          <Route path="/story/:projectId" element={<StoryPage />} />
          <Route path="/editor" element={<EditorPage />} />
          <Route path="/workflows" element={<WorkflowsPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </QueryClientProvider>
  );
}
