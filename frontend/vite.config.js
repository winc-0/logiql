import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 开发态由 Vite 把 /api 代理到后端，省掉跨域配置；
// 后端地址可用 ZHISHU_API 覆盖（默认 8010，与 main.py 的启动端口一致）
const target = process.env.ZHISHU_API || "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        configure: (proxy) => {
          // SSE 是长连接流式响应，这两个头不加的话代理可能缓冲住整段流，
          // 前端就看不到逐步推进的执行过程
          proxy.on("proxyReq", (proxyReq) => {
            proxyReq.setHeader("Cache-Control", "no-cache");
            proxyReq.setHeader("Connection", "keep-alive");
          });
        },
      },
    },
  },
  build: {
    // 产物丢给 FastAPI 的 StaticFiles 托管，与后端同端口，生产态不需要代理
    outDir: "dist",
    emptyOutDir: true,
  },
});
