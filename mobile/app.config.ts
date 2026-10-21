import type { ExpoConfig, ConfigContext } from "expo/config";

/**
 * E0.1 workspace scaffold. Bundle ids `org.pymoney.mobile` (decided);
 * device floor iOS 16+ / Android 10+ (API 29); phone widths only.
 */
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: "PyMoney",
  slug: "pymoney-mobile",
  version: "0.1.0",
  orientation: "portrait",
  icon: "./assets/icon.png",
  userInterfaceStyle: "automatic",
  scheme: "pymoney",
  ios: {
    bundleIdentifier: "org.pymoney.mobile",
    supportsTablet: false,
    deploymentTarget: "16.0",
    infoPlist: {
      NSCameraUsageDescription:
        "Photograph a bill or receipt to attach to a transaction.",
      NSPhotoLibraryUsageDescription:
        "Choose a photo of a bill or receipt to attach to a transaction.",
      NSPhotoLibraryAddUsageDescription: "Save a receipt image to your library.",
    },
  },
  android: {
    package: "org.pymoney.mobile",
    // Android 10+
    minSdkVersion: 29,
    adaptiveIcon: {
      backgroundColor: "#0b62b8",
      foregroundImage: "./assets/android-icon-foreground.png",
      backgroundImage: "./assets/android-icon-background.png",
      monochromeImage: "./assets/android-icon-monochrome.png",
    },
    permissions: ["CAMERA", "READ_MEDIA_IMAGES", "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE"],
    predictiveBackGestureEnabled: false,
  },
  web: {
    favicon: "./assets/favicon.png",
  },
  plugins: ["expo-asset", "expo-router", "expo-secure-store"],
  extra: {
    // Keep in sync with the server's bundle ids / store listings when E9.3 lands.
    deviceFloor: { ios: "16.0", android: 29 },
  },
});
