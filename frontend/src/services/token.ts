// JWT storage helpers (localStorage). The token is written by the auth store
// on login/register and read by the axios interceptor to attach the
// Authorization header. Kept in its own module so services/api.ts and
// services/auth.ts share the same key without a circular import.

const TOKEN_KEY = "tunisia_energy_token";

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}
