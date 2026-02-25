import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.swissjobhunter.app",
  appName: "SwissJobHunter",
  webDir: "dist",
  server: {
    // In development, point to the Vite dev server
    // Uncomment for live-reload on device:
    // url: "http://192.168.1.X:5173",
    // cleartext: true,
  },
  plugins: {
    PushNotifications: {
      presentationOptions: ["badge", "sound", "alert"],
    },
    StatusBar: {
      style: "DARK",
    },
  },
};

export default config;
