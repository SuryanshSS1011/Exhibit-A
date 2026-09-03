import { spawn } from "node:child_process";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";
import { localRootConfigured, refuse, resolveLocalRepo } from "@/lib/api-guard";

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

// The sandbox is not a caller's choice. A repository reaching this route is untrusted,
// and running its suite outside a container executes whatever its conftest imports on
// this host. The engine sandboxes by default, so this route simply never opts out.

// One request spawns an engine that builds images and runs suites, so unbounded
// concurrency is a trivial way to exhaust the host.
const MAX_CONCURRENT = Math.max(1, Number(process.env.EXHIBIT_A_MAX_CONCURRENT ?? "2"));
const RUN_TIMEOUT_MS = Math.max(1, Number(process.env.EXHIBIT_A_RUN_TIMEOUT_S ?? "1800")) * 1000;
let active = 0;

interface Body {
  repo?: string;
  fixed?: string;
  control?: string;
  repoUrl?: string;
  baseSha?: string;
  fixSha?: string;
  controlSha?: string;
  claim?: string;
  expect?: string;
  replay?: "proven" | "silence";
}

const REPLAY_CASES = {
  proven: path.resolve(ENGINE_DIR, "..", "fixtures", "cases", "inventory_proven.json"),
  silence: path.resolve(ENGINE_DIR, "..", "fixtures", "cases", "inventory_silence.json"),
} as const;

export async function POST(req: NextRequest) {
  const refusal = refuse(req);
  if (refusal) return refusal;

  let body: Body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  const replay = body.replay ? REPLAY_CASES[body.replay] : undefined;
  if (body.replay && !replay) {
    return NextResponse.json({ error: "unknown sealed Case" }, { status: 400 });
  }
  // A local path over HTTP otherwise means "copy and run any directory on this host".
  const localPaths: Partial<Record<"repo" | "fixed" | "control", string>> = {};
  if (!replay) {
    for (const key of ["repo", "fixed", "control"] as const) {
      const value = body[key];
      if (!value) continue;
      const resolved = resolveLocalRepo(value);
      if (!resolved) {
        return NextResponse.json(
          {
            error: localRootConfigured()
              ? `${key} path resolves outside the configured local root`
              : "local repository paths are not accepted",
            hint: localRootConfigured()
              ? undefined
              : "set EXHIBIT_A_LOCAL_ROOT to the directory investigations may read",
          },
          { status: 400 },
        );
      }
      localPaths[key] = resolved;
    }
  }

  const hasLocal = Boolean(localPaths.repo);
  const hasRemote = Boolean(body.repoUrl && body.baseSha && body.fixSha);
  if (!replay && (!body.claim || hasLocal === hasRemote)) {
    return NextResponse.json(
      { error: "provide claim and exactly one local or remote repository source" },
      { status: 400 },
    );
  }

  const outDir = path.join(ENGINE_DIR, ".exhibit-a", "cases");
  const args = replay
    ? ["-m", "exhibit_a.cli", "repro", "--replay", replay, "--events"]
    : [
        "-m",
        "exhibit_a.cli",
        "repro",
        hasRemote ? body.repoUrl! : localPaths.repo!,
        "--claim",
        body.claim!,
        "--out",
        outDir,
        "--events",
      ];
  if (!replay) {
    if (body.expect) args.push("--expect", body.expect);
    if (localPaths.fixed) args.push("--fixed", localPaths.fixed);
    if (localPaths.control) args.push("--control", localPaths.control);
    if (hasRemote) args.push("--base-sha", body.baseSha!, "--fix-sha", body.fixSha!);
    if (body.controlSha) args.push("--control-sha", body.controlSha);
  }

  if (active >= MAX_CONCURRENT) {
    return NextResponse.json(
      { error: "too many investigations in flight", hint: "retry when one finishes" },
      { status: 429, headers: { "Retry-After": "30" } },
    );
  }
  active += 1;

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let buffer = "";
      let stderr = "";
      let closed = false;
      let sawCase = false;
      let released = false;
      const release = () => {
        if (!released) {
          released = true;
          active -= 1;
        }
      };
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

      const child = spawn(PYTHON, args, { cwd: ENGINE_DIR, detached: true });
      const stopChild = () => {
        try {
          // Negative pid signals the group: the engine spawns docker and pytest below it.
          process.kill(-child.pid!, "SIGKILL");
        } catch {
          child.kill("SIGKILL");
        }
      };
      const budget = setTimeout(() => {
        send(
          { event: "error", error: `engine exceeded its ${RUN_TIMEOUT_MS / 1000}s budget` },
          "error",
        );
        stopChild();
      }, RUN_TIMEOUT_MS);
      child.stdout.on("data", (chunk) => {
        buffer += chunk.toString();
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        lines.forEach(consumeLine);
      });
      child.stderr.on("data", (d) => (stderr += d.toString()));
      child.on("error", (error) => {
        clearTimeout(budget);
        release();
        send({ event: "error", error: error.message }, "error");
        close();
      });
      child.on("close", (code) => {
        clearTimeout(budget);
        release();
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

      req.signal.addEventListener("abort", stopChild, { once: true });
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
