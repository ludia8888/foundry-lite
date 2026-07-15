import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 기본 개발 서버는 백엔드 CORS 계약과 같은 4173을 사용한다. 병렬 검증이나
// 최신 런타임 확인은 별도 포트 + same-origin /api proxy로 CORS 범위를 넓히지 않는다.
export default defineConfig(() => {
  const port = Number.parseInt(
    process.env.FOUNDRY_LITE_FRONTEND_PORT ?? "4173",
    10,
  );
  const apiProxyTarget = process.env.FOUNDRY_LITE_API_PROXY_TARGET?.trim();

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "./src"),
      },
    },
    server: {
      port,
      strictPort: true,
      proxy: apiProxyTarget
        ? {
            "/api": {
              target: apiProxyTarget,
              changeOrigin: true,
            },
          }
        : undefined,
    },
  };
});
