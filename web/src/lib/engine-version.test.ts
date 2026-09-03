import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { engineVersion } from "./engine-version";

describe("engineVersion", () => {
  it("matches the version the Python package declares", () => {
    const source = readFileSync(
      path.join(process.cwd(), "..", "engine", "exhibit_a", "__init__.py"),
      "utf8",
    );
    const declared = /^__version__\s*=\s*["']([^"']+)["']/m.exec(source)?.[1];

    expect(declared).toBeTruthy();
    expect(engineVersion()).toBe(declared);
  });

  it("is a version, not a placeholder", () => {
    expect(engineVersion()).not.toBe("unknown");
    expect(engineVersion()).toMatch(/^\d+\.\d+/);
  });
});
