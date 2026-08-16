import { create } from "zustand";
import { fetchMe, loginUser, registerUser, type AuthUser } from "../services/auth";
import { clearStoredToken, getStoredToken, storeToken } from "../services/token";

export type AuthStatus = "loading" | "anonymous" | "authenticated";

interface AuthState {
  user: AuthUser | null;
  status: AuthStatus;
  /** Last auth error message (localized by the caller or raw API detail). */
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => void;
  /** Restore a session from the stored token on app boot. */
  init: () => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: "anonymous",
  error: null,

  login: async (email, password) => {
    set({ error: null, status: "loading" });
    try {
      const res = await loginUser(email, password);
      storeToken(res.access_token);
      set({ user: res.user, status: "authenticated" });
    } catch (e) {
      set({ status: "anonymous", error: (e as Error).message });
      throw e;
    }
  },

  register: async (email, password, displayName) => {
    set({ error: null, status: "loading" });
    try {
      const res = await registerUser(email, password, displayName);
      storeToken(res.access_token);
      set({ user: res.user, status: "authenticated" });
    } catch (e) {
      set({ status: "anonymous", error: (e as Error).message });
      throw e;
    }
  },

  logout: () => {
    clearStoredToken();
    set({ user: null, status: "anonymous", error: null });
  },

  init: async () => {
    const token = getStoredToken();
    if (!token) {
      set({ status: "anonymous" });
      return;
    }
    set({ status: "loading" });
    try {
      const user = await fetchMe();
      set({ user, status: "authenticated" });
    } catch {
      // Stale/expired token: drop it and fall back to anonymous.
      clearStoredToken();
      set({ user: null, status: "anonymous" });
    }
  },

  clearError: () => set({ error: null }),
}));
