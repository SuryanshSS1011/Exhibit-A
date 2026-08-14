import { describe, expect, it } from "vitest";
import type { Case, Verdict } from "./case";
import { isEvidence, isProven, normalizeCasePayload } from "./case";
import { prosecutorExample, withheldConcern } from "./prosecutor-example";

function caseWith(verdict: Verdict): Case {
  return { ...prosecutorExample, verdict };
}

describe("verdict helpers", () => {
  it("isProven is true only for a full flip", () => {
    expect(isProven(caseWith("VERIFIED"))).toBe(true);
    expect(isProven(caseWith("PARTIAL"))).toBe(false);
    expect(isProven(caseWith("FAILED"))).toBe(false);
    expect(isProven(caseWith("UNCERTAIN"))).toBe(false);
  });

  it("isEvidence covers both admissible tiers but not silence", () => {
    expect(isEvidence(caseWith("VERIFIED"))).toBe(true);
    expect(isEvidence(caseWith("PARTIAL"))).toBe(true);
    expect(isEvidence(caseWith("FAILED"))).toBe(false);
    expect(isEvidence(caseWith("UNCERTAIN"))).toBe(false);
  });

  it("normalizes legacy stored verdict and disposition values", () => {
    expect(
      normalizeCasePayload({ verdict: "REPRODUCED", disposition: "REPRODUCED" }),
    ).toEqual({ verdict: "PARTIAL", disposition: "PARTIAL" });
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
    expect(prosecutorExample.evidence_strength?.coverage).toBeLessThan(1);
    expect(prosecutorExample.evidence_strength?.mutation.score).toBe(0);
  });

  it("keeps the unprovable concern out of the review comment", () => {
    expect(withheldConcern.reason).toMatch(/no review comment posted/i);
  });
});
