import { useCallback, useEffect, useState } from "react";
import { Capacitor } from "@capacitor/core";

/**
 * Push notification abstraction.
 * - Native: uses @capacitor/push-notifications
 * - Web: uses the Web Push API (Notification API)
 */
export function usePushNotifications() {
  const [permission, setPermission] = useState("default");
  const [token, setToken] = useState(null);
  const isNative = Capacitor.isNativePlatform();

  const requestPermission = useCallback(async () => {
    if (isNative) {
      const { PushNotifications } = await import(
        "@capacitor/push-notifications"
      );
      const result = await PushNotifications.requestPermissions();
      setPermission(result.receive);
      if (result.receive === "granted") {
        await PushNotifications.register();
      }
      return result.receive;
    }

    // Web fallback
    if (!("Notification" in window)) {
      setPermission("denied");
      return "denied";
    }
    const result = await Notification.requestPermission();
    setPermission(result);
    return result;
  }, [isNative]);

  useEffect(() => {
    if (!isNative) {
      if ("Notification" in window) {
        setPermission(Notification.permission);
      }
      return;
    }

    let cleanup = () => {};

    (async () => {
      const { PushNotifications } = await import(
        "@capacitor/push-notifications"
      );
      const perm = await PushNotifications.checkPermissions();
      setPermission(perm.receive);

      const registration = await PushNotifications.addListener(
        "registration",
        (t) => setToken(t.value),
      );
      cleanup = () => registration.remove();
    })();

    return () => cleanup();
  }, [isNative]);

  return { permission, token, requestPermission, isNative };
}
