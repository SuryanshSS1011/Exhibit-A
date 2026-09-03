/**
 * Attach the API token the server injected into the document.
 *
 * The token gates the engine-driving routes against callers that never loaded the page.
 * It is visible to anyone who can load the page, so it is a deployment guard rather than
 * user authentication — see `api-guard.ts`.
 */
export function apiHeaders(extra: Record<string, string> = {}): Record<string, string> {
  if (typeof document === "undefined") return extra;
  const token = document
    .querySelector('meta[name="exhibit-a-api-token"]')
    ?.getAttribute("content");
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}
