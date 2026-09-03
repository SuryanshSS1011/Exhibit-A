import { timingSafeEqual } from "node:crypto";
import path from "node:path";
import { NextResponse } from "next/server";

/**
 * Server-side gate for the routes that spawn work or write to disk.
 *
 * These routes drive the engine and persist private research labels, so an unconfigured
 * deployment must be inert rather than open. The token is a deployment guard, not user
 * authentication: the browser UI is handed the same token, so the API is exactly as
 * private as the page that carries it. Put real authentication in front of the page for
 * any deployment reachable by someone who should not run investigations.
 */

const MIN_TOKEN_LENGTH = 16;

export function configuredToken(): string | null {
  const token = process.env.EXHIBIT_A_API_TOKEN;
  return token && token.length >= MIN_TOKEN_LENGTH ? token : null;
}

interface HeaderBearing {
  headers: { get(name: string): string | null };
}

/** Returns a response to send when the request must be refused, or null to proceed. */
export function refuse(req: HeaderBearing): NextResponse | null {
  const expected = configuredToken();
  if (!expected) {
    return NextResponse.json(
      {
        error: "This endpoint is disabled until an API token is configured",
        hint: `set EXHIBIT_A_API_TOKEN to at least ${MIN_TOKEN_LENGTH} characters`,
      },
      { status: 503 },
    );
  }
  const header = req.headers.get("authorization") ?? "";
  const offered = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!offered || !constantTimeEquals(offered, expected)) {
    return NextResponse.json({ error: "missing or invalid API token" }, { status: 401 });
  }
  return null;
}

function constantTimeEquals(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  // timingSafeEqual throws on a length mismatch, which would itself leak the length.
  if (left.length !== right.length) {
    timingSafeEqual(left, left);
    return false;
  }
  return timingSafeEqual(left, right);
}

/**
 * Resolve a caller-supplied local repository path inside the configured root.
 *
 * Without a root, a local path over HTTP means "copy and run any directory on this
 * host", so the default is to accept none. Returns null when the path is not allowed.
 */
export function resolveLocalRepo(value: string): string | null {
  const root = process.env.EXHIBIT_A_LOCAL_ROOT;
  if (!root) return null;
  const base = path.resolve(root);
  const target = path.resolve(base, value);
  if (target !== base && !target.startsWith(base + path.sep)) return null;
  return target;
}

export function localRootConfigured(): boolean {
  return Boolean(process.env.EXHIBIT_A_LOCAL_ROOT);
}
