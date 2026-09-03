import { afterEach, describe, expect, it, vi } from "vitest";
import { refuse, resolveLocalRepo } from "./api-guard";

const TOKEN = "0123456789abcdef0123";

function request(authorization?: string) {
  const headers = new Headers();
  if (authorization) headers.set("authorization", authorization);
  return { headers };
}

afterEach(() => vi.unstubAllEnvs());

describe("refuse", () => {
  it("disables the route until a token is configured", () => {
    vi.stubEnv("EXHIBIT_A_API_TOKEN", "");
    expect(refuse(request(`Bearer ${TOKEN}`))?.status).toBe(503);
  });

  it("treats a too-short token as unconfigured rather than accepting it", () => {
    vi.stubEnv("EXHIBIT_A_API_TOKEN", "short");
    expect(refuse(request("Bearer short"))?.status).toBe(503);
  });

  it("rejects a request with no credentials", () => {
    vi.stubEnv("EXHIBIT_A_API_TOKEN", TOKEN);
    expect(refuse(request())?.status).toBe(401);
  });

  it("rejects a wrong token, including one that is merely a prefix", () => {
    vi.stubEnv("EXHIBIT_A_API_TOKEN", TOKEN);
    expect(refuse(request("Bearer wrong-token-entirely"))?.status).toBe(401);
    expect(refuse(request(`Bearer ${TOKEN.slice(0, -1)}`))?.status).toBe(401);
  });

  it("ignores a non-bearer scheme", () => {
    vi.stubEnv("EXHIBIT_A_API_TOKEN", TOKEN);
    expect(refuse(request(`Basic ${TOKEN}`))?.status).toBe(401);
  });

  it("admits the configured token", () => {
    vi.stubEnv("EXHIBIT_A_API_TOKEN", TOKEN);
    expect(refuse(request(`Bearer ${TOKEN}`))).toBeNull();
  });
});

describe("resolveLocalRepo", () => {
  it("accepts no local path until a root is configured", () => {
    vi.stubEnv("EXHIBIT_A_LOCAL_ROOT", "");
    expect(resolveLocalRepo("/tmp/anything")).toBeNull();
  });

  it("resolves a path inside the root", () => {
    vi.stubEnv("EXHIBIT_A_LOCAL_ROOT", "/srv/repos");
    expect(resolveLocalRepo("project")).toBe("/srv/repos/project");
  });

  it("refuses traversal and absolute escapes", () => {
    vi.stubEnv("EXHIBIT_A_LOCAL_ROOT", "/srv/repos");
    expect(resolveLocalRepo("../secrets")).toBeNull();
    expect(resolveLocalRepo("/etc/passwd")).toBeNull();
    expect(resolveLocalRepo("project/../../etc")).toBeNull();
  });

  it("does not treat a sibling with a shared prefix as inside the root", () => {
    vi.stubEnv("EXHIBIT_A_LOCAL_ROOT", "/srv/repos");
    expect(resolveLocalRepo("/srv/repos-private/x")).toBeNull();
  });
});
