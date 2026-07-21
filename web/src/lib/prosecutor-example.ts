import type { Case } from "@/lib/case";

const testCode = `from slicer import last_n

def test_last_n_keeps_final_element():
    assert last_n([1, 2, 3, 4], 2) == [3, 4]
`;

const failLog = `E   AssertionError: assert [3] == [3, 4]
E     Right contains one more item: 4
FAILED test_repro.py::test_last_n_keeps_final_element
1 failed in 0.03s`;

export const prosecutorExample: Case = {
  id: "fixture-pr-17",
  mode: "prosecutor",
  repo: "exhibit-a/demo-slicer",
  base_commit: "a11ce17",
  target_commit: "badc0de",
  target_state: "pr_head",
  claim_text: "PR #17 drops the final element from every last_n result.",
  hypotheses: [
    {
      text: "Changing the slice stop to -1 excludes the final element.",
      rejected: false,
      reason: null,
    },
  ],
  root_cause_narrative: "The new exclusive stop index omits the final element.",
  intent_judgment: "unintended",
  intent_rationale:
    "The PR description promises to preserve last_n behavior while simplifying its slice.",
  intent_model: "gpt-5.6-sol",
  declared_behavior_delta: "Preserve last_n output while simplifying its slice.",
  declared_delta_sources: ["pr_description"],
  test_file: {
    path: "test_repro.py",
    code: testCode,
    language: "python",
    framework: "pytest",
  },
  run_command: "python3 -m pytest -x -q test_repro.py",
  evidence: {
    fail_log: failLog,
    fail_signature: "AssertionError: assert [3] == [3, 4]",
    pass_log: "1 passed in 0.02s",
    control_log: "1 passed in 0.02s",
    reruns: 5,
    deterministic: true,
    runs: [],
  },
  verdict: "PROVEN",
  disposition: "PROVEN_REGRESSION",
  silence_reason: null,
  license_name: "MIT",
  fail_to_pass: ["test_repro.py"],
  pass_to_pass: [],
  existing_suite_passed: true,
  suite_gap: true,
  created_at: "2026-07-21T00:00:00Z",
};

export const withheldConcern = {
  hypothesis: "The same change may alter n=0 behavior.",
  reason: "Could not produce a signature-matching fail-to-pass test; no review comment posted.",
};
