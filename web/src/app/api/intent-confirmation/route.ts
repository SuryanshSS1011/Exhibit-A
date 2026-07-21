import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";

const ROOT = path.resolve(
  process.cwd(),
  "..",
  "engine",
  ".exhibit-a",
  "research",
  "intent-confirmations",
);
const CASE_ID = /^[A-Za-z0-9_-]{1,64}$/;

interface Body {
  caseId?: string;
  choice?: "intended" | "regression";
  rawVerdict?: string;
  disposition?: string;
  intentModel?: string | null;
  declaredBehaviorDelta?: string | null;
}

// POST-only and local: labels are never exposed by a read endpoint or sent to a PR.
export async function POST(req: NextRequest) {
  let body: Body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (
    !body.caseId ||
    !CASE_ID.test(body.caseId) ||
    !body.choice ||
    !["intended", "regression"].includes(body.choice)
  ) {
    return NextResponse.json({ error: "invalid intent confirmation" }, { status: 400 });
  }
  const recordedAt = new Date().toISOString();
  const record = {
    schema_version: "intent-confirmation/v1",
    engine_version: "0.0.1",
    model_version: body.intentModel ?? "not_assessed",
    recorded_at: recordedAt,
    case_id: body.caseId,
    human_label: body.choice,
    raw_verdict: body.rawVerdict,
    disposition: body.disposition,
    declared_behavior_delta: body.declaredBehaviorDelta ?? null,
  };
  try {
    await mkdir(ROOT, { recursive: true });
    await writeFile(
      path.join(ROOT, `${body.caseId}-${randomUUID()}.json`),
      JSON.stringify(record, null, 2),
      { flag: "wx", mode: 0o600 },
    );
  } catch {
    return NextResponse.json({ error: "could not persist private label" }, { status: 500 });
  }
  return NextResponse.json({ recorded: true, choice: body.choice }, { status: 201 });
}
