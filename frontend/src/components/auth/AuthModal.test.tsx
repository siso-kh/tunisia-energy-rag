import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import AuthModal from "./AuthModal";
import { useAuthStore } from "../../store/authStore";
import * as authService from "../../services/auth";

vi.mock("../../services/auth", () => ({
  loginUser: vi.fn(),
  registerUser: vi.fn(),
  fetchMe: vi.fn(),
}));

const mockedAuth = vi.mocked(authService);

// i18n: keep the real module (so src/i18n.ts still initializes) and just
// stub useTranslation to the real translator (fr is the test default).
import i18n from "../../i18n";
vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  return {
    ...actual,
    useTranslation: () => ({ t: (k: string) => i18n.t(k) }),
  };
});

const USER = { id: "u1", email: "user@example.com", display_name: "Amine" };

describe("AuthModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useAuthStore.setState({ user: null, status: "anonymous", error: null });
  });

  it("renders nothing when closed", () => {
    render(<AuthModal open={false} onClose={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the login form when open", () => {
    render(<AuthModal open onClose={vi.fn()} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText(/Adresse e-mail/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Mot de passe/)).toBeInTheDocument();
  });

  it("switches to the register tab and shows the display-name field", () => {
    render(<AuthModal open onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /S'inscrire/ }));
    expect(screen.getByLabelText(/Nom affiché/)).toBeInTheDocument();
    expect(screen.getByText(/8 caractères minimum/)).toBeInTheDocument();
  });

  it("submits login with the entered credentials and closes", async () => {
    mockedAuth.loginUser.mockResolvedValue({
      access_token: "tok-1",
      token_type: "bearer",
      user: USER,
    });
    const onClose = vi.fn();
    render(<AuthModal open onClose={onClose} />);

    fireEvent.change(screen.getByLabelText(/Adresse e-mail/), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/Mot de passe/), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Se connecter/ }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockedAuth.loginUser).toHaveBeenCalledWith("user@example.com", "password123");
    expect(localStorage.getItem("tunisia_energy_token")).toBe("tok-1");
  });

  it("registers with a display name", async () => {
    mockedAuth.registerUser.mockResolvedValue({
      access_token: "tok-2",
      token_type: "bearer",
      user: USER,
    });
    render(<AuthModal open onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /S'inscrire/ }));
    fireEvent.change(screen.getByLabelText(/Nom affiché/), {
      target: { value: "Amine" },
    });
    fireEvent.change(screen.getByLabelText(/Adresse e-mail/), {
      target: { value: "amine@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/Mot de passe/), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Créer le compte/ }));

    await waitFor(() =>
      expect(mockedAuth.registerUser).toHaveBeenCalledWith("amine@example.com", "password123", "Amine")
    );
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("surfaces the backend error instead of closing", async () => {
    mockedAuth.loginUser.mockRejectedValue(new Error("Invalid email or password."));
    const onClose = vi.fn();
    render(<AuthModal open onClose={onClose} />);

    fireEvent.change(screen.getByLabelText(/Adresse e-mail/), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/Mot de passe/), {
      target: { value: "wrong" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Se connecter/ }));

    await waitFor(() => expect(screen.getByText("Invalid email or password.")).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("closes via the X button", () => {
    const onClose = vi.fn();
    render(<AuthModal open onClose={onClose} />);
    fireEvent.click(screen.getByLabelText(/Fermer/));
    expect(onClose).toHaveBeenCalled();
  });
});
