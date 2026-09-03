import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * The engine's version, read from the engine rather than restated here.
 *
 * Research records claim which engine produced them, so a literal in TypeScript is a
 * second source of truth that drifts the moment the Python package is bumped. Reading it
 * keeps one source; failing to read it yields "unknown", which is more honest than a
 * version we cannot actually confirm.
 */
const VERSION_LINE = /^__version__\s*=\s*["']([^"']+)["']/m;

export function engineVersion(): string {
  try {
    const source = readFileSync(
      path.join(process.cwd(), "..", "engine", "exhibit_a", "__init__.py"),
      "utf8",
    );
    return VERSION_LINE.exec(source)?.[1] ?? "unknown";
  } catch {
    return "unknown";
  }
}
