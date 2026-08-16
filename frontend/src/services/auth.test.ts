import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import api from "./api";
import { registerUser, loginUser, fetchMe } from "./auth";
import { clearStoredToken, storeToken } from "./token";
import type { AxiosResponse } from "axios";

function okResponse<T>(data: T): AxiosResponse<T> {
  return { data, status: 200, statusText: "OK", headers: {}, config: {} as never };
}

// Minimal shape we assert on from the captured request config.
interface CapturedRequest {
  headers?: { Authorization?: string };
}

/** Run a real request through the shared instance so interceptors fire, capturing the final config. */
async function requestWithCapture(): Promise<CapturedRequest> {
  let captured: CapturedRequest | undefined;
  await api.get("/auth/me", {
    adapter: (config) => {
      captured = { headers: config.headers as unknown as { Authorization?: string } };
      return Promise.resolve(okResponse({ ok: true }));
    },
  });
  return captured ?? {};
}

describe("auth service + interceptor", () => {
  beforeEach(() => {
    clearStoredToken();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("attaches the Bearer token to requests when one is stored", async () => {
    storeToken("jwt-token-123");
    const captured = await requestWithCapture();
    expect(captured.headers?.Authorization).toBe("Bearer jwt-token-123");
  });

  it("sends no Authorization header when anonymous", async () => {
    const captured = await requestWithCapture();
    expect(captured.headers?.Authorization).toBeUndefined();
  });

  it("registerUser POSTs to /auth/register with the right payload", async () => {
    const postSpy = vi
      .spyOn(api, "post")
      .mockResolvedValue(okResponse({ access_token: "t", token_type: "bearer", user: { id: "u" } }));
    await registerUser("User@Example.com", "password123", "Amine");
    expect(postSpy).toHaveBeenCalledWith("/auth/register", {
      email: "User@Example.com",
      password: "password123",
      display_name: "Amine",
    });
  });

  it("loginUser POSTs to /auth/login", async () => {
    const postSpy = vi
      .spyOn(api, "post")
      .mockResolvedValue(okResponse({ access_token: "t", token_type: "bearer", user: { id: "u" } }));
    await loginUser("user@example.com", "password123");
    expect(postSpy).toHaveBeenCalledWith("/auth/login", {
      email: "user@example.com",
      password: "password123",
    });
  });

  it("fetchMe GETs /auth/me", async () => {
    const getSpy = vi
      .spyOn(api, "get")
      .mockResolvedValue(okResponse({ id: "u1", email: "a@b.c", display_name: null }));
    const me = await fetchMe();
    expect(getSpy).toHaveBeenCalledWith("/auth/me");
    expect(me.email).toBe("a@b.c");
  });
});
