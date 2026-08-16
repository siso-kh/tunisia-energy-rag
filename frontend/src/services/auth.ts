import api from "./api";

export interface AuthUser {
  id: string;
  email: string | null;
  display_name: string | null;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

/** Register a new account; returns the JWT + user (token stored by the caller). */
export async function registerUser(
  email: string,
  password: string,
  displayName?: string | null
): Promise<AuthResponse> {
  const { data } = await api.post<AuthResponse>("/auth/register", {
    email,
    password,
    display_name: displayName || null,
  });
  return data;
}

/** Login with email + password; returns the JWT + user. */
export async function loginUser(email: string, password: string): Promise<AuthResponse> {
  const { data } = await api.post<AuthResponse>("/auth/login", { email, password });
  return data;
}

/** Fetch the current user for a stored token (the interceptor attaches it). */
export async function fetchMe(): Promise<AuthUser> {
  const { data } = await api.get<AuthUser>("/auth/me");
  return data;
}
