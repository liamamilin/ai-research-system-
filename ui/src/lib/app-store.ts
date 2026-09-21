import { create } from "zustand";

interface AppState {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  closeSidebar: () => void;
  setSidebar: (open: boolean) => void;
}

export const useAppStore = create<AppState>((set) => ({
  // Collapsed by default on small screens; open on desktop.
  sidebarOpen: typeof window !== "undefined" ? window.innerWidth >= 1024 : true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  closeSidebar: () => set({ sidebarOpen: false }),
  setSidebar: (open) => set({ sidebarOpen: open }),
}));
