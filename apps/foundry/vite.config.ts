import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 백엔드 CORS(ALLOWED_BROWSER_ORIGINS)가 4173만 허용하므로 dev 서버도 4173을 사용한다.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    port: 4173,
    strictPort: true,
  },
});
