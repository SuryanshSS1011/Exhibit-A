import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The API routes were the only executable surface here with no tests at all, and one of
 * them spawns the engine. These cover the gate rather than the happy path: an
 * unconfigured or unauthenticated deployment must serve nothing, and the two routes that
 * read private research output must be as closed as the two that spawn work.
 */

const TOKEN = "0123456789abcdef0123";

function get(authorization?: string): Request {
  const headers = new Headers();
  if (authorization) headers.set("authorization", authorization);
  return new Request("http://localhost/api", { headers });
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe.each([
  ["silence", "EXHIBIT_A_SILENCE_LOG"],
  ["self-audit", "EXHIBIT_A_RESEARCH_DASHBOARD"],
] as const)("the %s route serves private research output", (name, flag) => {
  async function route() {
    return (await import(`./${name}/route`)) as { GET(req: Request): Promise<Response> };
  }

  it("is inert until a token is configured, even when the feature is enabled", async () => {
    vi.stubEnv(flag, "1");
    vi.stubEnv("EXHIBIT_A_API_TOKEN", "");

    const response = await (await route()).GET(get(`Bearer ${TOKEN}`));

    expect(response.status).toBe(503);
  });

  it("refuses an unauthenticated caller even when the feature is enabled", async () => {
    vi.stubEnv(flag, "1");
    vi.stubEnv("EXHIBIT_A_API_TOKEN", TOKEN);

    const response = await (await route()).GET(get());

    expect(response.status).toBe(401);
  });

  it("refuses a wrong token", async () => {
    vi.stubEnv(flag, "1");
    vi.stubEnv("EXHIBIT_A_API_TOKEN", TOKEN);

    const response = await (await route()).GET(get("Bearer 0000000000000000wrong"));

    expect(response.status).toBe(401);
  });

  it("stays closed on the feature flag once the token is right", async () => {
    // Both gates are required. The flag says this deployment means to serve it; the
    // token says the caller is the deployment's own UI.
    vi.stubEnv(flag, "");
    vi.stubEnv("EXHIBIT_A_API_TOKEN", TOKEN);

    const response = await (await route()).GET(get(`Bearer ${TOKEN}`));

    expect(response.status).toBe(403);
  });
});

describe("the investigate route drives the engine", () => {
  async function route() {
    return (await import("./investigate/route")) as {
      POST(req: Request): Promise<Response>;
    };
  }

  function post(authorization?: string): Request {
    const headers = new Headers({ "content-type": "application/json" });
    if (authorization) headers.set("authorization", authorization);
    return new Request("http://localhost/api/investigate", {
      method: "POST",
      headers,
      body: JSON.stringify({ claim: "anything" }),
    });
  }

  it("is inert until a token is configured", async () => {
    vi.stubEnv("EXHIBIT_A_API_TOKEN", "");

    expect((await (await route()).POST(post(`Bearer ${TOKEN}`))).status).toBe(503);
  });

  it("refuses an unauthenticated caller before spawning anything", async () => {
    vi.stubEnv("EXHIBIT_A_API_TOKEN", TOKEN);

    expect((await (await route()).POST(post())).status).toBe(401);
  });
});
