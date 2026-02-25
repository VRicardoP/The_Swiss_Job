import { create } from "zustand";

const TOKEN_KEY = "swissjob_token";
const REFRESH_KEY = "swissjob_refresh_token";

const storage = typeof localStorage !== "undefined" ? localStorage : null;

const useAuthStore = create((set) => ({
  token: storage?.getItem(TOKEN_KEY) ?? null,
  refreshToken: storage?.getItem(REFRESH_KEY) ?? null,
  user: null,
  hydrated: false,

  setAuth(token, refreshToken, user = null) {
    storage?.setItem(TOKEN_KEY, token);
    storage?.setItem(REFRESH_KEY, refreshToken);
    set({ token, refreshToken, user });
  },

  setUser(user) {
    set({ user });
  },

  setHydrated(hydrated) {
    set({ hydrated });
  },

  logout() {
    storage?.removeItem(TOKEN_KEY);
    storage?.removeItem(REFRESH_KEY);
    set({ token: null, refreshToken: null, user: null, hydrated: true });
  },
}));

export default useAuthStore;
