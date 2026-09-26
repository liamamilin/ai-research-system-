import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { LoginPage } from "./Login";
import { useAuthStore } from "@/lib/auth-store";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
}

let calls: { url: string; init?: RequestInit }[] = [];
let loginResult: "ok" | "fail" = "ok";

function mockFetch() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    if (String(url).includes("/api/setup/status")) {
      return jsonResponse({ needs_admin: false, llm_configured: true, search_configured: true });
    }
    if (String(url).includes("/api/auth/login")) {
      if (loginResult === "fail") {
        return { ok: false, status: 401, statusText: "unauthorized",
                 text: async () => JSON.stringify({ error: { code: "invalid_credentials", message: "用户名或密码错误" } }) } as Response;
      }
      return jsonResponse({ user: { id: 1, username: "admin", role: "admin" }, csrf_token: "t" });
    }
    return jsonResponse({});
  }));
}

const ADMIN = {
  id: 1, username: "admin", role: "admin" as const,
  created_at: null, last_login_at: null,
};

/** Renders the page with a /login -> / route pair so navigation is observable. */
function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<h1>控制台首页</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  loginResult = "ok";
  useAuthStore.setState({ user: null, loading: false });
  localStorage.clear();
  mockFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function signIn(remember = true) {
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "pw" } });
  const box = screen.getByLabelText(/记住我/) as HTMLInputElement;
  if (box.checked !== remember) fireEvent.click(box);
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await waitFor(() => expect(calls.some((c) => c.url.includes("/api/auth/login"))).toBe(true));
}

describe("Login page", () => {
  it("offers remember-me and keeps the username out of the password field", async () => {
    renderLogin();
    const box = screen.getByLabelText(/记住我/) as HTMLInputElement;
    expect(box).toBeTruthy();
    expect(box.checked).toBe(true);
    await signIn(true);
    const body = JSON.parse(calls.find((c) => c.url.includes("/api/auth/login"))!.init!.body as string);
    expect(body).toEqual({ username: "admin", password: "pw", remember: true });
  });

  it("sends remember:false when the box is cleared", async () => {
    renderLogin();
    await signIn(false);
    const body = JSON.parse(calls.find((c) => c.url.includes("/api/auth/login"))!.init!.body as string);
    expect(body.remember).toBe(false);
  });

  it("remembers only the username, never the password", async () => {
    renderLogin();
    await signIn(true);
    const stored = Object.fromEntries(
      Object.keys(localStorage).map((k) => [k, localStorage.getItem(k)]),
    );
    expect(JSON.stringify(stored)).toContain("admin");
    expect(JSON.stringify(stored)).not.toContain("pw");
  });

  it("clears the remembered username when remember-me is off", async () => {
    localStorage.setItem("arec.last-username", "admin");
    renderLogin();
    // The field is prefilled from the previous choice.
    expect((screen.getByLabelText("用户名") as HTMLInputElement).value).toBe("admin");
    await signIn(false);
    expect(localStorage.getItem("arec.last-username")).toBeNull();
  });

  it("prefills the username on a later visit", () => {
    localStorage.setItem("arec.last-username", "admin");
    renderLogin();
    expect((screen.getByLabelText("用户名") as HTMLInputElement).value).toBe("admin");
    // The password is never prefilled, whatever the checkbox says.
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
  });

  it("skips the form when a session is already live", async () => {
    useAuthStore.setState({ user: ADMIN, loading: false });
    renderLogin();
    // A valid refresh cookie means the user is signed in; asking again is the
    // complaint remember-me exists to remove.
    expect(await screen.findByText("控制台首页")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "登录" })).toBeNull();
  });

  it("keeps a rejected password visible instead of navigating away", async () => {
    loginResult = "fail";
    renderLogin();
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "bad" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("用户名或密码错误")).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
  });
});
