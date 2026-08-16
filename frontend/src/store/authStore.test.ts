import { describe, it, expect, beforeEach, vi } from "vitest";
import { useAuthStore } from "./authStore";
import * as authService from "../services/auth";
import * as tokenService from "../services/token";

vi.mock("../services/auth", () => ({
  loginUser: vi.fn(),
  registerUser: vi.fn(),
  fetchMe: vi.fn(),
}));

const mockedAuth = vi.mocked(authService);

const USER = { id: "u1", email: "user@example.com", display_name: "Amine" };

describe("authStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useAuthStore.setState({ user: null, status: "anonymous", error: null });
  });

  it("login stores the token and sets the user", async () => {
    mockedAuth.loginUser.mockResolvedValue({ access_token: "tok-1", token_type: "bearer", user: USER });

    await useAuthStore.getState().login("user@example.com", "password123");

    expect(localStorage.getItem("tunisia_energy_token")).toBe("tok-1");
    expect(useAuthStore.getState().user).toEqual(USER);
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().error).toBeNull();
  });

  it("login failure stays anonymous and surfaces the error", async () => {
    mockedAuth.loginUser.mockRejectedValue(new Error("Invalid email or password."));

    await expect(
      useAuthStore.getState().login("user@example.com", "wrong")
    ).rejects.toThrow("Invalid email or password.");

    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().error).toBe("Invalid email or password.");
    expect(localStorage.getItem("tunisia_energy_token")).toBeNull();
  });

  it("register stores the token and sets the user", async () => {
    mockedAuth.registerUser.mockResolvedValue({ access_token: "tok-2", token_type: "bearer", user: USER });

    await useAuthStore.getState().register("user@example.com", "password123", "Amine");

    expect(localStorage.getItem("tunisia_energy_token")).toBe("tok-2");
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().user).toEqual(USER);
  });

  it("logout clears the token and resets to anonymous", () => {
    localStorage.setItem("tunisia_energy_token", "tok-1");
    useAuthStore.setState({ user: USER, status: "authenticated" });

    useAuthStore.getState().logout();

    expect(localStorage.getItem("tunisia_energy_token")).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("init without a stored token stays anonymous", async () => {
    await useAuthStore.getState().init();
    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(mockedAuth.fetchMe).not.toHaveBeenCalled();
  });

  it("init with a valid stored token restores the session", async () => {
    localStorage.setItem("tunisia_energy_token", "tok-1");
    mockedAuth.fetchMe.mockResolvedValue(USER);

    await useAuthStore.getState().init();

    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().user).toEqual(USER);
  });

  it("init with an invalid token clears it and falls back to anonymous", async () => {
    localStorage.setItem("tunisia_energy_token", "stale-token");
    mockedAuth.fetchMe.mockRejectedValue(new Error("Not authenticated."));

    await useAuthStore.getState().init();

    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useAuthStore.getState().user).toBeNull();
    expect(localStorage.getItem("tunisia_energy_token")).toBeNull();
  });

  it("clearError resets the error message", () => {
    useAuthStore.setState({ error: "boom" });
    useAuthStore.getState().clearError();
    expect(useAuthStore.getState().error).toBeNull();
  });

  // Token helpers live in services/token; sanity-check the roundtrip so the
  // store and the axios interceptor share the same key.
  it("token helpers roundtrip through the shared localStorage key", () => {
    expect(tokenService.getStoredToken()).toBeNull();
    tokenService.storeToken("abc");
    expect(tokenService.getStoredToken()).toBe("abc");
    tokenService.clearStoredToken();
    expect(tokenService.getStoredToken()).toBeNull();
  });
});
