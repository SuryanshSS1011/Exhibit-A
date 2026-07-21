import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

const CASE_DIR = path.resolve(process.cwd(), "..", "engine", ".exhibit-a", "cases");

export const dynamic = "force-dynamic";

export async function GET() {
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
