import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    maxWorkers: 2,
    testTimeout: 15_000,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@rhetorica/ui": path.resolve(__dirname, "../../packages/ui/src/index.ts"),
      "server-only": path.resolve(__dirname, "./src/test/server-only.ts"),
    },
  },
});
