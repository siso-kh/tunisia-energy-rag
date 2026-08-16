import axios from "axios";
import { getStoredToken } from "./token";

// Base URL defaults to the dev proxy (same-origin). In production the
// Nginx container proxies /api to the backend, so relative paths work.
const api = axios.create({
  baseURL: "/api",
  timeout: 60_000,
  headers: {
    "Content-Type": "application/json",
  },
});

// Attach the user JWT (if any) to every request so authenticated endpoints
// (conversations, chat persistence, outages) work without per-call wiring.
// Anonymous sessions simply send no header and hit the demo-user fallback.
api.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

// Simple error normalizer so callers can surface friendly messages.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = error?.response?.data?.detail;
    error.message = typeof detail === "string" ? detail : error.message;
    return Promise.reject(error);
  }
);

export default api;
