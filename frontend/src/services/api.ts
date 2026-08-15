import axios from "axios";

// Base URL defaults to the dev proxy (same-origin). In production the
// Nginx container proxies /api to the backend, so relative paths work.
const api = axios.create({
  baseURL: "/api",
  timeout: 60_000,
  headers: {
    "Content-Type": "application/json",
  },
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
