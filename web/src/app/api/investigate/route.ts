import { spawn } from "node:child_process";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";

/**
 * POST /api/investigate — stream Evidence Engine progress and the final Case.
 *
 * The web layer does not reimplement the engine; it drives the Python one. The CLI
 * emits one JSON object per line. This route wraps those events as SSE so
 * the case-file UI can show the agent try, fail, retry, and finally render the
 * deterministic Case returned by the engine.
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
    "--events",
  ];
  if (body.expect) args.push("--expect", body.expect);
  if (body.fixed) args.push("--fixed", body.fixed);
  if (hasRemote) args.push("--base-sha", body.baseSha!, "--fix-sha", body.fixSha!);
  if (body.docker) args.push("--docker");

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let buffer = "";
      let stderr = "";
      let closed = false;
      let sawCase = false;
      const send = (payload: unknown, event = "message") => {
        if (!closed) {
          controller.enqueue(
            encoder.encode(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`),
          );
        }
      };
      const close = () => {
        if (!closed) {
          closed = true;
          controller.close();
        }
      };
      const consumeLine = (line: string) => {
        if (!line.trim()) return;
        try {
          const payload = JSON.parse(line);
          if (payload.event === "case") sawCase = true;
          send(payload, payload.event ?? "message");
        } catch {
          send({ event: "error", error: "engine emitted invalid progress data" }, "error");
        }
      };

      const child = spawn(PYTHON, args, { cwd: ENGINE_DIR });
      child.stdout.on("data", (chunk) => {
        buffer += chunk.toString();
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        lines.forEach(consumeLine);
      });
      child.stderr.on("data", (d) => (stderr += d.toString()));
      child.on("error", (error) => {
        send({ event: "error", error: error.message }, "error");
        close();
      });
      child.on("close", (code) => {
        if (buffer) consumeLine(buffer);
        if (code !== 0 && code !== 1) {
          send(
            { event: "error", error: `engine exited ${code}`, stderr: stderr.trim() },
            "error",
          );
        } else if (!sawCase) {
          send({ event: "error", error: "engine produced no final Case" }, "error");
        }
        close();
      });

      req.signal.addEventListener("abort", () => child.kill("SIGTERM"), { once: true });
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
