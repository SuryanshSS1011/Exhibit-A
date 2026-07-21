import { describe, expect, it } from "vitest";
import type { Case, Verdict } from "./case";
import { isEvidence, isProven } from "./case";
import { prosecutorExample, withheldConcern } from "./prosecutor-example";

function caseWith(verdict: Verdict): Case {
  return { ...prosecutorExample, verdict };
}

describe("verdict helpers", () => {
  it("isProven is true only for a full flip", () => {
    expect(isProven(caseWith("PROVEN"))).toBe(true);
    expect(isProven(caseWith("REPRODUCED"))).toBe(false);
    expect(isProven(caseWith("INSUFFICIENT_EVIDENCE"))).toBe(false);
  });

  it("isEvidence covers both admissible tiers but not silence", () => {
    expect(isEvidence(caseWith("PROVEN"))).toBe(true);
    expect(isEvidence(caseWith("REPRODUCED"))).toBe(true);
    expect(isEvidence(caseWith("INSUFFICIENT_EVIDENCE"))).toBe(false);
  });
});

describe("prosecutor example integrity", () => {
  it("only speaks with a proven flip that fills a suite gap", () => {
    // The whole point of the Prosecutor gate: a comment exists only with proof.
    expect(isProven(prosecutorExample)).toBe(true);
    expect(prosecutorExample.test_file).not.toBeNull();
    expect(prosecutorExample.evidence.pass_log.length).toBeGreaterThan(0);
    // Additive-only: the existing suite passed, so this is a genuine gap.
    expect(prosecutorExample.existing_suite_passed).toBe(true);
    expect(prosecutorExample.suite_gap).toBe(true);
  });

  it("keeps the unprovable concern out of the review comment", () => {
    expect(withheldConcern.reason).toMatch(/no review comment posted/i);
  });
});
