import { useCallback } from "react";
import { Capacitor } from "@capacitor/core";

/**
 * Offline storage abstraction.
 * - Native: uses @capacitor/preferences (persistent key-value store)
 * - Web: uses localStorage
 */
export function useOfflineStorage() {
  const isNative = Capacitor.isNativePlatform();

  const getItem = useCallback(
    async (key) => {
      if (isNative) {
        const { Preferences } = await import("@capacitor/preferences");
        const { value } = await Preferences.get({ key });
        return value ? JSON.parse(value) : null;
      }
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    },
    [isNative],
  );

  const setItem = useCallback(
    async (key, value) => {
      const serialized = JSON.stringify(value);
      if (isNative) {
        const { Preferences } = await import("@capacitor/preferences");
        await Preferences.set({ key, value: serialized });
        return;
      }
      localStorage.setItem(key, serialized);
    },
    [isNative],
  );

  const removeItem = useCallback(
    async (key) => {
      if (isNative) {
        const { Preferences } = await import("@capacitor/preferences");
        await Preferences.remove({ key });
        return;
      }
      localStorage.removeItem(key);
    },
    [isNative],
  );

  const clear = useCallback(async () => {
    if (isNative) {
      const { Preferences } = await import("@capacitor/preferences");
      await Preferences.clear();
      return;
    }
    localStorage.clear();
  }, [isNative]);

  return { getItem, setItem, removeItem, clear, isNative };
}
