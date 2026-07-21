import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

const REPORT_DIR = path.resolve(
  process.env.EXHIBIT_A_SELF_AUDIT_DIR ??
    path.join(process.cwd(), "..", "engine", ".exhibit-a", "research", "self-audit"),
);
const ENABLED = process.env.EXHIBIT_A_RESEARCH_DASHBOARD === "1";

export const dynamic = "force-dynamic";

export async function GET() {
  if (!ENABLED) {
    return NextResponse.json(
      {
        error: "Research dashboard is private by default",
        hint: "set EXHIBIT_A_RESEARCH_DASHBOARD=1 in a trusted local environment",
      },
      { status: 403 },
    );
  }
  try {
    const names = (await readdir(REPORT_DIR)).filter((name) => name.endsWith(".json"));
    const reports = await Promise.all(
      names.map(async (name) => JSON.parse(await readFile(path.join(REPORT_DIR, name), "utf8"))),
    );
    const latest = reports
      .filter((report) => report?.schema_version === "self-audit/v1")
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
    return NextResponse.json(
      { report: latest ?? null },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    if (isNodeError(error) && error.code === "ENOENT") {
      return NextResponse.json({ report: null });
    }
    return NextResponse.json({ error: "could not read self-audit reports" }, { status: 500 });
  }
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}
