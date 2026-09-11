import { Outlet } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { Toasts } from '@/components/ui';
import { AssistantPanel } from '@/features/assistant/AssistantPanel';

export function AppShell() {
  return (
    <div className="flex h-full overflow-hidden bg-surface-0">
      <div className="hidden md:block">
        <Sidebar />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="flex-1 overflow-y-auto scroll-thin">
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="mx-auto w-full max-w-[1680px] p-4 lg:p-6"
          >
            <Outlet />
          </motion.div>
        </main>
      </div>
      <AssistantPanel />
      <Toasts />
    </div>
  );
}
