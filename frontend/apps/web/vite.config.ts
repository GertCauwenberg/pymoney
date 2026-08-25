import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// In development the API is reached through Vite's proxy under /api, so the
// browser sees one origin and no CORS or cookie-domain configuration is needed.
// In production, either serve dist/ from the same origin as the API (a reverse
// proxy mapping /api -> uvicorn) or set VITE_API_BASE to the API's URL and list
// the web origin in PYMONEY_CORS_ORIGINS.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.PYMONEY_API_URL ?? "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
    test: {
      name: "web",
      globals: true,
      environment: "jsdom",
      setupFiles: ["./test/setup.ts"],
      include: ["test/**/*.test.tsx", "test/**/*.test.ts"],
    },
  };
});
