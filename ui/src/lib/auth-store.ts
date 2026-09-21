import { create } from "zustand";
import type { User } from "@/api/types";
import * as authApi from "@/api";

interface AuthState {
  user: User | null;
  loading: boolean;
  setUser: (user: User | null) => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: true,

  setUser: (user) => set({ user, loading: false }),

  login: async (username, password) => {
    const resp = await authApi.login(username, password);
    set({ user: resp.user, loading: false });
  },

  logout: async () => {
    await authApi.logout();
    set({ user: null, loading: false });
  },

  checkAuth: async () => {
    try {
      const user = await authApi.getMe();
      set({ user, loading: false });
    } catch {
      set({ user: null, loading: false });
    }
  },
}));
