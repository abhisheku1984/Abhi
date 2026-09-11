import { NavLink } from 'react-router-dom';
import {
  Clapperboard, Cpu, Folder, Gauge, Images, LayoutDashboard, Library, Mic2, Music4,
  Sparkles, UserSquare2, Users, Workflow, HardDrive, Settings, ChevronLeft, Film, Layers,
} from 'lucide-react';
import { clsx } from '@/lib/format';
import { useAppStore } from '@/app/store';

const NAV = [
  { group: 'Create', items: [
    { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { to: '/create', label: 'Create', icon: Sparkles },
    { to: '/story', label: 'Story & Storyboard', icon: Clapperboard },
    { to: '/editor', label: 'Editor', icon: Film },
    { to: '/workflows', label: 'Workflows', icon: Workflow },
  ]},
  { group: 'Library', items: [
    { to: '/projects', label: 'Projects', icon: Folder },
    { to: '/assets', label: 'All assets', icon: Library },
    { to: '/assets?kind=image', label: 'Images', icon: Images },
    { to: '/assets?kind=video', label: 'Videos', icon: Film },
    { to: '/avatars', label: 'Avatars', icon: UserSquare2 },
    { to: '/characters', label: 'Characters', icon: Users },
    { to: '/voices', label: 'Voices', icon: Mic2 },
    { to: '/audio', label: 'Audio', icon: Music4 },
  ]},
  { group: 'System', items: [
    { to: '/jobs', label: 'Jobs', icon: Layers },
    { to: '/models', label: 'Models', icon: Cpu },
    { to: '/admin', label: 'Admin', icon: HardDrive },
    { to: '/settings', label: 'Settings', icon: Settings },
  ]},
];

export function Sidebar() {
  const collapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggle = useAppStore((s) => s.toggleSidebar);
  const branding = useAppStore((s) => s.branding);

  return (
    <aside
      className={clsx(
        'relative flex h-full shrink-0 flex-col border-r border-edge bg-surface-1/60 backdrop-blur-xl transition-all duration-300',
        collapsed ? 'w-[68px]' : 'w-[248px]',
      )}
    >
      <div className="flex h-16 items-center gap-2.5 px-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-brand/15 text-brand">
          {branding.logo_url ? (
            <img src={branding.logo_url} alt="" className="h-6 w-6 rounded-lg object-contain" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-ink">{branding.product_name}</p>
            <p className="truncate text-[10px] uppercase tracking-wider text-ink-faint">Creative Studio</p>
          </div>
        )}
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-3 pb-6 scroll-thin">
        {NAV.map((section) => (
          <div key={section.group}>
            {!collapsed && <p className="label mb-1.5 px-2">{section.group}</p>}
            <div className="space-y-0.5">
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    clsx(
                      'group flex items-center gap-3 rounded-xl px-2.5 py-2 text-sm transition-all',
                      isActive
                        ? 'bg-brand/12 text-brand'
                        : 'text-ink-dim hover:bg-surface-2/70 hover:text-ink',
                    )
                  }
                >
                  <item.icon className="h-4 w-4 shrink-0" />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <button
        onClick={toggle}
        className="absolute -right-3 top-20 hidden h-6 w-6 items-center justify-center rounded-full border border-edge bg-surface-2 text-ink-faint transition hover:text-ink lg:flex"
        aria-label="Toggle sidebar"
      >
        <ChevronLeft className={clsx('h-3.5 w-3.5 transition-transform', collapsed && 'rotate-180')} />
      </button>

      {!collapsed && (
        <div className="border-t border-edge p-3">
          <p className="text-[10px] leading-relaxed text-ink-faint">{branding.footer}</p>
        </div>
      )}
    </aside>
  );
}
