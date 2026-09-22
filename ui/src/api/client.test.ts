import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, setAuthErrorHandler } from "./client";

function fakeResponse(status: number, body?: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `status-${status}`,
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
  } as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  document.cookie = "ai_research_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  setAuthErrorHandler(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api()", () => {
  it("sends GET with credentials and no CSRF header", async () => {
    fetchMock.mockResolvedValueOnce(fakeResponse(200, { ok: true }));
    const data = await api<{ ok: boolean }>("/api/things");

    expect(data).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/things");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
    expect(init.body).toBeUndefined();
    expect(init.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("adds CSRF header and JSON body on unsafe methods", async () => {
    document.cookie = "ai_research_csrf=csrf-123";
    fetchMock.mockResolvedValueOnce(fakeResponse(200, { ok: true }));
    await api("/api/things", { method: "POST", body: { a: 1 } });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-123");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ a: 1 }));
  });

  it("serializes query params and skips undefined", async () => {
    fetchMock.mockResolvedValueOnce(fakeResponse(200, []));
    await api("/api/reports", { query: { page: 2, q: "x", skip: undefined } });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/reports?page=2&q=x");
  });

  it("appends query params to a path that already has a query string", async () => {
    fetchMock.mockResolvedValueOnce(fakeResponse(200, []));
    await api("/api/reports?sort=date", { query: { page: 1 } });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/reports?sort=date&page=1");
  });

  it("returns undefined for 204", async () => {
    fetchMock.mockResolvedValueOnce(fakeResponse(204));
    expect(await api("/api/things")).toBeUndefined();
  });

  it("maps error payloads to ApiError", async () => {
    fetchMock.mockResolvedValueOnce(fakeResponse(403, {
      error: { code: "csrf_failed", message: "CSRF 校验失败", details: { a: 1 } },
    }));

    const err = await api("/api/things", { method: "POST" })
      .then(() => null)
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    if (!(err instanceof ApiError)) throw new Error("expected ApiError");
    expect(err.status).toBe(403);
    expect(err.code).toBe("csrf_failed");
    expect(err.message).toBe("CSRF 校验失败");
    expect(err.details).toEqual({ a: 1 });
  });

  it("refreshes once on 401 and retries the original request", async () => {
    document.cookie = "ai_research_csrf=csrf-123";
    fetchMock
      .mockResolvedValueOnce(fakeResponse(401, { error: { code: "expired", message: "x" } }))
      .mockResolvedValueOnce(fakeResponse(200, { ok: true }))
      .mockResolvedValueOnce(fakeResponse(200, { ok: true }));

    const data = await api("/api/things");

    expect(data).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/refresh");
    expect(fetchMock.mock.calls[1][1].method).toBe("POST");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/things");
  });

  it("fires the auth error handler when refresh fails", async () => {
    const onAuthError = vi.fn();
    setAuthErrorHandler(onAuthError);
    fetchMock
      .mockResolvedValueOnce(fakeResponse(401, { error: { code: "expired", message: "x" } }))
      .mockResolvedValueOnce(fakeResponse(401, { error: { code: "no_refresh", message: "y" } }));

    await expect(api("/api/things")).rejects.toBeInstanceOf(ApiError);
    expect(onAuthError).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("wraps network errors as ApiError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(api("/api/things")).rejects.toMatchObject({
      code: "network_error",
      status: 0,
    });
  });
});
