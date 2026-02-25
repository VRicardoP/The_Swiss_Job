import { create } from "zustand";

const TOKEN_KEY = "swissjob_token";

const useAuthStore = create((set) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: null,

  setAuth(token, user = null) {
    localStorage.setItem(TOKEN_KEY, token);
    set({ token, user });
  },

  logout() {
    localStorage.removeItem(TOKEN_KEY);
    set({ token: null, user: null });
  },
}));

export default useAuthStore;
