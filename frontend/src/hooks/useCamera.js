import { useCallback, useState } from "react";
import { Capacitor } from "@capacitor/core";

/**
 * Camera abstraction for profile photo / CV photo.
 * - Native: uses @capacitor/camera
 * - Web: uses file input fallback
 */
export function useCamera() {
  const [photo, setPhoto] = useState(null);
  const isNative = Capacitor.isNativePlatform();

  const takePhoto = useCallback(async () => {
    if (isNative) {
      const { Camera, CameraResultType, CameraSource } = await import(
        "@capacitor/camera"
      );
      const image = await Camera.getPhoto({
        quality: 80,
        allowEditing: false,
        resultType: CameraResultType.DataUrl,
        source: CameraSource.Prompt, // Let user choose camera or gallery
      });
      setPhoto(image.dataUrl);
      return image.dataUrl;
    }

    // Web fallback: file input
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "image/*";
      input.capture = "environment";
      input.onchange = (e) => {
        const file = e.target.files?.[0];
        if (!file) {
          resolve(null);
          return;
        }
        const reader = new FileReader();
        reader.onload = () => {
          setPhoto(reader.result);
          resolve(reader.result);
        };
        reader.readAsDataURL(file);
      };
      input.click();
    });
  }, [isNative]);

  return { photo, takePhoto, isNative };
}
