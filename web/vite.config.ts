/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Бэкенд Студии (uv run python -m studio.server --dev) – на 8765; Vite проксирует к нему API и файлы.
const API = process.env.STUDIO_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  base: "./",
  // elkjs (раскладка) – один неделимый кусок ~1,4 МБ, грузится лениво.
  build: { chunkSizeWarningLimit: 1600 },
  server: {
    proxy: {
      "/api": API,
      "/files": API,
    },
  },
  test: {
    environment: "node",
    // forks на этой Windows-машине виснут на старте – потоки работают
    pool: "threads",
    include: ["src/**/*.test.ts"],
    testTimeout: 60_000,
  },
});
