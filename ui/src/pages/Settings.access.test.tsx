import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "./Settings";
import { useAuthStore } from "@/lib/auth-store";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
}

const LAN_ON = {
  enabled: true, lan_access: true, local_url: "http://127.0.0.1:8765/",
  lan_url: "http://192.168.2.105:8765/", lan_address: "192.168.2.105",
  public_port: 8765, app_port: 8766, secure_cookies: false,
};
const LAN_OFF = { ...LAN_ON, lan_access: false, lan_url: "" };
const NO_GATEWAY = { ...LAN_OFF, enabled: false };

let gatewayPayload = LAN_ON;
let putResult: unknown = null;

function mockFetch() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url);
    if (path.includes("/api/gateway")) {
      if (init?.method === "PUT") return jsonResponse(putResult ?? gatewayPayload);
      return jsonResponse(gatewayPayload);
    }
    if (path.includes("/api/audit")) return jsonResponse({ entries: [] });
    if (path.includes("/api/users")) return jsonResponse({ users: [] });
    if (path.includes("/api/logs")) return jsonResponse({ lines: [] });
    // The 系统配置 tab renders on mount too, so its two calls have to be shaped
    // correctly or the page throws before the card can be asserted on.
    if (path.includes("/api/config/secrets")) return jsonResponse({ secrets: [] });
    if (path.includes("/api/config/system")) return jsonResponse({ system: {}, mtime: 0 });
    return jsonResponse({});
  }));
}

beforeEach(() => {
  gatewayPayload = LAN_ON;
  putResult = null;
  useAuthStore.setState({
    user: { id: 1, username: "admin", role: "admin", created_at: null, last_login_at: null },
    loading: false,
  });
  mockFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Settings access card", () => {
  it("keeps both links readable without expanding anything", async () => {
    render(<SettingsPage />);
    // The lookup must not require a click: that was the whole point of the card.
    expect(await screen.findByDisplayValue("http://192.168.2.105:8765/")).toBeTruthy();
    expect(screen.getByDisplayValue("http://127.0.0.1:8765/")).toBeTruthy();
    expect(screen.getByLabelText("复制手机链接")).toBeTruthy();
    expect(screen.getByLabelText("复制本机链接")).toBeTruthy();
  });

  it("sits at the bottom, after the tab content", async () => {
    render(<SettingsPage />);
    await screen.findByDisplayValue("http://127.0.0.1:8765/");
    const card = screen.getByText("访问地址");
    const tabs = screen.getByText("系统配置");
    // It is a lookup, not a headline: it must come after the tabs.
    expect(card.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it("stays collapsed by default", async () => {
    const { container } = render(<SettingsPage />);
    await screen.findByDisplayValue("http://127.0.0.1:8765/");
    // A <details> keeps its children in the DOM while hiding them, so assert on
    // the open state rather than on the LAN switch being absent.
    const details = container.querySelector("details");
    expect(details).toBeTruthy();
    expect(details?.hasAttribute("open")).toBe(false);
  });

  it("marks the card as reachable from a phone when LAN access is on", async () => {
    render(<SettingsPage />);
    expect(await screen.findByText(/手机可访问/)).toBeTruthy();
  });

  it("offers no phone link when LAN access is off", async () => {
    gatewayPayload = LAN_OFF;
    render(<SettingsPage />);
    expect(await screen.findByDisplayValue("http://127.0.0.1:8765/")).toBeTruthy();
    // A link that would not open is worse than no link.
    expect(screen.queryByLabelText("复制手机链接")).toBeNull();
    expect(screen.getByText(/仅本机/)).toBeTruthy();
  });

  it("warns that LAN access is cleartext HTTP once expanded", async () => {
    gatewayPayload = LAN_OFF;
    render(<SettingsPage />);
    await screen.findByDisplayValue("http://127.0.0.1:8765/");
    fireEvent.click(screen.getByText("访问地址"));
    expect(await screen.findByText(/HTTP 明文/)).toBeTruthy();
  });

  it("tells the user a restart is needed after switching LAN on", async () => {
    gatewayPayload = LAN_OFF;
    putResult = { ...LAN_ON, changed: true, restart_required: true,
                  restart_hint: "bash scripts/install_launchd.sh --uninstall" };
    render(<SettingsPage />);
    await screen.findByDisplayValue("http://127.0.0.1:8765/");
    fireEvent.click(screen.getByText("访问地址"));
    fireEvent.click(await screen.findByLabelText(/允许同一 WiFi 下的设备访问/));
    expect(await screen.findByText(/重启网关才生效/)).toBeTruthy();
    expect(screen.getByText(/install_launchd.sh --uninstall/)).toBeTruthy();
  });

  it("shows how to enable the gateway when it is not installed", async () => {
    gatewayPayload = NO_GATEWAY;
    render(<SettingsPage />);
    await screen.findByDisplayValue("http://127.0.0.1:8765/");
    fireEvent.click(screen.getByText("访问地址"));
    expect(await screen.findByText(/常驻网关未启用/)).toBeTruthy();
  });

  it("copies the link to the clipboard", async () => {
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    render(<SettingsPage />);
    fireEvent.click(await screen.findByLabelText("复制手机链接"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("http://192.168.2.105:8765/"));
    expect(await screen.findByTitle("已复制")).toBeTruthy();
  });
});
