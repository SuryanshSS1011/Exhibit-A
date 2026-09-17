import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The application imports through the `@/` alias that tsconfig declares, and vitest ran
// with no config at all, so anything reaching for `@/` failed to load. Every module that
// used it was therefore untestable, which is why the four API routes had no tests: the
// tooling could not reach them. Declared here rather than by adding a plugin, since one
// alias does not need a dependency.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
