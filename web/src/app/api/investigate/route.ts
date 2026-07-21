import { spawn } from "node:child_process";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";

/**
 * POST /api/investigate — run the Evidence Engine on a claim and return the Case.
 *
 * The web layer does NOT reimplement the engine; it drives the Python one. For the
 * MVP we invoke the CLI as a module and read the emitted Case JSON back. A real
 * v1 would stream the sandbox log live (SSE) so the UI can show the agent try,
 * fail, and retry — the most compelling demo moment (plan §4). This route is the
 * seam where that streaming plugs in.
 */

const ENGINE_DIR = path.resolve(process.cwd(), "..", "engine");
const PYTHON = process.env.EXHIBIT_A_PYTHON ?? "python3";

interface Body {
  repo?: string;
  fixed?: string;
  repoUrl?: string;
  baseSha?: string;
  fixSha?: string;
  claim: string;
  expect?: string;
  docker?: boolean;
}

export async function POST(req: NextRequest) {
  let body: Body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  const hasLocal = Boolean(body.repo);
  const hasRemote = Boolean(body.repoUrl && body.baseSha && body.fixSha);
  if (!body.claim || hasLocal === hasRemote) {
    return NextResponse.json(
      { error: "provide claim and exactly one local or remote repository source" },
      { status: 400 },
    );
  }

  const outDir = path.join(ENGINE_DIR, ".exhibit-a", "cases");
  const args = [
    "-m",
    "exhibit_a.cli",
    "repro",
    hasRemote ? body.repoUrl! : body.repo!,
    "--claim",
    body.claim,
    "--out",
    outDir,
    "--json",
  ];
  if (body.expect) args.push("--expect", body.expect);
  if (body.fixed) args.push("--fixed", body.fixed);
  if (hasRemote) args.push("--base-sha", body.baseSha!, "--fix-sha", body.fixSha!);
  if (body.docker) args.push("--docker");

  const result = await runEngine(args);
  if (result.error) {
    return NextResponse.json(
      { error: result.error, stderr: result.stderr },
      { status: 500 },
    );
  }

  // The CLI prints a header then the full Case JSON (with --json). Extract the JSON.
  const jsonStart = result.stdout.indexOf("{");
  if (jsonStart === -1) {
    return NextResponse.json(
      { error: "engine produced no Case JSON", stdout: result.stdout },
      { status: 500 },
    );
  }
  try {
    const caseObj = JSON.parse(result.stdout.slice(jsonStart));
    return NextResponse.json(caseObj);
  } catch {
    return NextResponse.json(
      { error: "could not parse Case JSON", stdout: result.stdout },
      { status: 500 },
    );
  }
}

function runEngine(
  args: string[],
): Promise<{ stdout: string; stderr: string; error?: string }> {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, args, { cwd: ENGINE_DIR });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString()));
    child.stderr.on("data", (d) => (stderr += d.toString()));
    child.on("error", (e) => resolve({ stdout, stderr, error: e.message }));
    child.on("close", (code) => {
      // The CLI exits 1 on INSUFFICIENT_EVIDENCE — that's a valid verdict, not an error.
      if (code !== 0 && code !== 1) {
        resolve({ stdout, stderr, error: `engine exited ${code}` });
      } else {
        resolve({ stdout, stderr });
      }
    });
  });
}
