/**
 * TypeScript mirror of the Python `Case` model (engine/exhibit_a/models/case.py).
 * Keep these two in sync — the API returns exactly `Case.to_dict()`.
 */

export type Mode = "prosecutor" | "detective";
export type Verdict = "VERIFIED" | "PARTIAL" | "FAILED" | "UNCERTAIN";
export type ExecutionTruth = "NOT_RUN" | "COMPLETED" | "FAILED";
export type GoalTruth = "VERIFIED" | "PARTIAL" | "FAILED" | "UNCERTAIN";
export type ReleaseTruth = "NOT_ASSESSED" | "SAFE" | "UNSAFE" | "UNCERTAIN";
export type TargetKind = "pr_head" | "synthesized_patch" | "base_only";
export type IntentJudgment = "not_assessed" | "intended" | "unintended" | "unclear";
export type Disposition =
  | "PROVEN_REGRESSION"
  | "BEHAVIOR_CHANGE"
  | "PARTIAL"
  | "FAILED"
  | "UNCERTAIN";

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

export interface ConnectorSecurity {
  source_access: string;
  network_access: string;
  isolation: string;
  credential_access: string;
}

export interface EvidenceProvenance {
  evidence_id: string;
  connector_id: string;
  connector_version: string;
  capability: "test_execution";
  source: string;
  source_revision: string | null;
  observed_at: string;
  source_updated_at: string | null;
  freshness: "point_in_time" | "cached" | "unknown";
  description: string;
  request_sha256: string;
  response_sha256: string;
  artifact_sha256: string;
  content_sha256: string;
  security: ConnectorSecurity;
}

export interface TruthAssessment {
  execution: ExecutionTruth;
  goal: GoalTruth;
  release: ReleaseTruth;
  execution_reason: string;
  goal_reason: string;
  release_reason: string;
}

export interface EvidenceMinimization {
  verified: boolean;
  attempts: number;
  accepted: number;
  original_lines: number;
  minimized_lines: number;
  reduction_ratio: number;
  reason: string | null;
}

export interface StrengthComponent {
  score: number | null;
  weight: number;
  basis: string;
}

export interface EvidenceStrength {
  schema_version: string;
  composite: number;
  coverage: number;
  mutation: StrengthComponent;
  signature: StrengthComponent;
  determinism: StrengthComponent;
  minimality: StrengthComponent;
  surface_distance: StrengthComponent;
}

export interface Hypothesis {
  text: string;
  rejected: boolean;
  reason: string | null;
}

export interface ProposalRun {
  operation: "propose" | "refine";
  provider: string;
  requested_model: string;
  confirmed_model: string;
  confirmed_version: string;
  output_sha256: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  tool_call_count: number;
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
  proposal_runs?: ProposalRun[];
  root_cause_narrative: string;
  intent_judgment: IntentJudgment;
  intent_rationale: string | null;
  intent_model: string | null;
  declared_behavior_delta: string | null;
  declared_delta_sources: string[];
  test_file: TestArtifact | null;
  original_test_file: TestArtifact | null;
  minimization: EvidenceMinimization | null;
  evidence_strength: EvidenceStrength | null;
  run_command: string;
  evidence: Evidence;
  evidence_sources?: EvidenceProvenance[];
  truth?: TruthAssessment;
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

export const isProven = (c: Case): boolean => c.verdict === "VERIFIED";
export const isVerified = isProven;
/** Any admissible tier: a full flip (VERIFIED) or a signature-matched PARTIAL. */
export const isEvidence = (c: Case): boolean =>
  c.verdict === "VERIFIED" || c.verdict === "PARTIAL";

const LEGACY_VERDICTS: Record<string, Verdict> = {
  PROVEN: "VERIFIED",
  REPRODUCED: "PARTIAL",
  INSUFFICIENT_EVIDENCE: "UNCERTAIN",
};

const LEGACY_DISPOSITIONS: Record<string, Disposition> = {
  REPRODUCED: "PARTIAL",
  INSUFFICIENT_EVIDENCE: "UNCERTAIN",
};

/** Upgrade persisted pre-migration Case values at a web/API ingestion boundary. */
export function normalizeCasePayload<T extends Record<string, unknown>>(value: T): T {
  const verdict = typeof value.verdict === "string" ? LEGACY_VERDICTS[value.verdict] : undefined;
  const disposition =
    typeof value.disposition === "string"
      ? LEGACY_DISPOSITIONS[value.disposition]
      : undefined;
  return {
    ...value,
    ...(verdict ? { verdict } : {}),
    ...(disposition ? { disposition } : {}),
  };
}
