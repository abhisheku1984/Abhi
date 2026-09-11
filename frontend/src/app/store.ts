import { create } from 'zustand';
import { Branding, User, endpoints, setAccessToken } from '@/lib/api';

interface Toast {
  id: string;
  kind: 'info' | 'success' | 'error';
  title: string;
  message?: string;
  action?: string;
}

interface AppState {
  user: User | null;
  branding: Branding;
  toasts: Toast[];
  sidebarCollapsed: boolean;
  assistantOpen: boolean;
  setUser: (user: User | null) => void;
  setBranding: (branding: Branding) => void;
  applyBranding: (branding: Branding) => void;
  toast: (toast: Omit<Toast, 'id'>) => void;
  dismiss: (id: string) => void;
  toggleSidebar: () => void;
  setAssistantOpen: (open: boolean) => void;
  logout: () => void;
}

const defaultBranding: Branding = {
  product_name: 'AI Creative Studio',
  company_name: 'AI Creative Studio',
  primary_color: '#7C5CFF',
  secondary_color: '#22D3EE',
  theme: 'dark',
  footer: 'AI Creative Studio — local-first generative media platform',
  logo_url: '',
  favicon_url: '',
  login_headline: 'Create anything with AI',
  login_subheadline: 'Image, video, avatar, voice and audio — in one studio.',
};

export const useAppStore = create<AppState>((set, get) => ({
  user: null,
  branding: defaultBranding,
  toasts: [],
  sidebarCollapsed: false,
  assistantOpen: false,

  setUser: (user) => set({ user }),

  setBranding: (branding) => {
    set({ branding });
    get().applyBranding(branding);
  },

  applyBranding: (branding) => {
    const root = document.documentElement;
    root.style.setProperty('--brand', hexToTriplet(branding.primary_color));
    root.style.setProperty('--accent', hexToTriplet(branding.secondary_color));
    root.dataset.theme = branding.theme === 'light' ? 'light' : 'dark';
    document.title = branding.product_name;
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (link && branding.favicon_url) link.href = branding.favicon_url;
  },

  toast: (toast) => {
    const id = Math.random().toString(36).slice(2);
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }));
    setTimeout(() => get().dismiss(id), toast.kind === 'error' ? 9000 : 5000);
  },

  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setAssistantOpen: (assistantOpen) => set({ assistantOpen }),

  logout: () => {
    setAccessToken(null);
    set({ user: null });
  },
}));

function hexToTriplet(hex: string) {
  const clean = (hex || '').replace('#', '');
  const full = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean;
  if (full.length !== 6) return '124 92 255';
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `${r} ${g} ${b}`;
}

export async function bootstrapBranding() {
  try {
    const { branding } = await endpoints.publicSettings();
    useAppStore.getState().setBranding(branding);
  } catch {
    useAppStore.getState().setBranding(defaultBranding);
  }
}

export { defaultBranding };
