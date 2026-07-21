/**
 * TypeScript mirror of the Python `Case` model (engine/exhibit_a/models/case.py).
 * Keep these two in sync — the API returns exactly `Case.to_dict()`.
 */

export type Mode = "prosecutor" | "detective";
export type Verdict = "PROVEN" | "REPRODUCED" | "INSUFFICIENT_EVIDENCE";
export type TargetKind = "pr_head" | "synthesized_patch" | "base_only";
export type IntentJudgment = "not_assessed" | "intended" | "unintended" | "unclear";
export type Disposition =
  | "PROVEN_REGRESSION"
  | "BEHAVIOR_CHANGE"
  | "REPRODUCED"
  | "INSUFFICIENT_EVIDENCE";

export interface TestArtifact {
  path: string;
  code: string;
  language: string;
  framework: string;
}

export interface RunResult {
  state: "base" | "target" | "control";
  exit_code: number;
  passed: boolean;
  log: string;
  signature: string | null;
  duration_s: number | null;
}

export interface Evidence {
  fail_log: string;
  fail_signature: string | null;
  pass_log: string;
  control_log: string;
  bisect_log: string;
  reruns: number;
  deterministic: boolean;
  runs: RunResult[];
}

export interface Hypothesis {
  text: string;
  rejected: boolean;
  reason: string | null;
}

export interface Case {
  id: string;
  mode: Mode;
  repo: string | null;
  base_commit: string | null;
  target_commit: string | null;
  culprit_commit: string | null;
  culprit_parent_commit: string | null;
  environment_ref: string | null;
  target_state: TargetKind;
  claim_text: string;
  hypotheses: Hypothesis[];
  root_cause_narrative: string;
  intent_judgment: IntentJudgment;
  intent_rationale: string | null;
  intent_model: string | null;
  declared_behavior_delta: string | null;
  declared_delta_sources: string[];
  test_file: TestArtifact | null;
  run_command: string;
  evidence: Evidence;
  verdict: Verdict;
  disposition: Disposition;
  silence_reason: string | null;
  license_name: string | null;
  fail_to_pass: string[];
  pass_to_pass: string[];
  existing_suite_passed: boolean | null;
  suite_gap: boolean | null;
  existing_suite_log: string;
  created_at: string;
}

export const isProven = (c: Case): boolean => c.verdict === "PROVEN";
/** Any admissible tier: a full flip (PROVEN) or a signature-matched REPRODUCED. */
export const isEvidence = (c: Case): boolean =>
  c.verdict === "PROVEN" || c.verdict === "REPRODUCED";
