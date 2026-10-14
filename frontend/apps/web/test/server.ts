/**
 * A fake API server for component tests: handlers keyed `"METHOD /path"`,
 * matched by prefix, returning `{ status?, body? }`. Every request is recorded
 * in `calls`, so a test can assert on what the component actually sent.
 */
export type Handler = (req: { method: string; path: string; body: unknown }) => { status?: number; body?: unknown };

export function server(handlers: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const path = url.pathname.replace(/^\/api/, "");
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    const key = Object.keys(handlers).find((k) => {
      const [m, p] = k.split(" ") as [string, string];
      return m === method && path.startsWith(p);
    });
    if (!key) return new Response(JSON.stringify({ error: { type: "NotFound", message: "x", status: 404 } }), { status: 404 });
    const { status = 200, body: out } = handlers[key]!({ method, path, body });
    return new Response(out === undefined ? null : JSON.stringify(out), { status: out === undefined ? 204 : status, headers: { "content-type": "application/json", "x-pymoney-revision": "4" } });
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}
