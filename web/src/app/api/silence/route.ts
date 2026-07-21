import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

const CASE_DIR = path.resolve(process.cwd(), "..", "engine", ".exhibit-a", "cases");

export const dynamic = "force-dynamic";

// The Silence Log lists hypotheses we could NOT prove. That is a low-confidence
// signal and, on real repos, can leak suspected-but-unconfirmed vulnerabilities.
// It is therefore PRIVATE BY DEFAULT: this endpoint only serves data when
// EXHIBIT_A_SILENCE_LOG=1 is explicitly set (a trusted dashboard context). It must
// never be posted to a public PR.
const SILENCE_LOG_ENABLED = process.env.EXHIBIT_A_SILENCE_LOG === "1";

export async function GET() {
  if (!SILENCE_LOG_ENABLED) {
    return NextResponse.json(
      {
        error: "Silence Log is private by default",
        hint: "set EXHIBIT_A_SILENCE_LOG=1 to enable it in a trusted dashboard context",
      },
      { status: 403 },
    );
  }
  let names: string[];
  try {
    names = await readdir(CASE_DIR);
  } catch (error) {
    if (isNodeError(error) && error.code === "ENOENT") {
      return NextResponse.json({ cases: [] });
    }
    return NextResponse.json({ error: "could not read case store" }, { status: 500 });
  }

  const cases = await Promise.all(
    names
      .filter((name) => name.endsWith(".json"))
      .map(async (name) => JSON.parse(await readFile(path.join(CASE_DIR, name), "utf8"))),
  );
  const silenceCases = cases
    .filter(
      (item) =>
        item &&
        typeof item === "object" &&
        item.verdict === "INSUFFICIENT_EVIDENCE",
    )
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));

  return NextResponse.json(
    { cases: silenceCases },
    { headers: { "Cache-Control": "no-store" } },
  );
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}
