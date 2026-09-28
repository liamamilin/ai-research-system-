import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShareLinks } from "./ShareLinks";
import type { ShareLink } from "@/api";

function link(over: Partial<ShareLink> = {}): ShareLink {
  return {
    jti: "abc123",
    path: "output/research/2026-09-28_AI.md",
    by: "admin",
    created_at: 1_700_000_000,
    expires_at: 1_700_000_000 + 7 * 86400,
    revoked_at: null,
    expired: false,
    state: "live",
    ...over,
  };
}

const listShareLinks = vi.fn();
const revokeShareLink = vi.fn();

vi.mock("@/api", async () => ({
  listShareLinks: (...a: unknown[]) => listShareLinks(...a),
  revokeShareLink: (...a: unknown[]) => revokeShareLink(...a),
}));

beforeEach(() => {
  listShareLinks.mockReset().mockResolvedValue({ links: [link()] });
  revokeShareLink.mockReset().mockResolvedValue({ ok: true, revoked: true });
});

afterEach(cleanup);

describe("ShareLinks", () => {
  it("lists a live link with the report it exposes", async () => {
    render(<ShareLinks />);
    const path = await screen.findByText(/2026-09-28_AI\.md/);
    // Scoped to the row: the header also counts "生效中".
    expect(path.closest("div")!.textContent).toContain("生效中");
  });

  it("revokes a link and stops offering it as live", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    // Model the server: after revoking, the listing reports it revoked.
    let revoked = false;
    listShareLinks.mockImplementation(async () => ({
      links: [link(revoked
        ? { state: "revoked", revoked_at: 1_700_000_500 }
        : {})],
    }));
    // The POST changes server state; the reload the component then does must
    // see it.
    revokeShareLink.mockImplementation(async () => {
      revoked = true;
      return { ok: true, revoked: true };
    });
    render(<ShareLinks />);
    fireEvent.click(await screen.findByRole("button", { name: /撤销/ }));

    await waitFor(() =>
      expect(revokeShareLink).toHaveBeenCalledWith("abc123"),
    );
    // The point of revoking is that the link stops working, so the row has to
    // change rather than sit there looking identical to a live one.
    await waitFor(() => expect(screen.getByText(/已撤销/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: /撤销$/ })).toBeNull();
  });

  it("does not offer to revoke something already revoked", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    listShareLinks.mockResolvedValue({
      links: [link({ state: "revoked", revoked_at: 1_700_000_500 })],
    });
    render(<ShareLinks />);
    expect(await screen.findByText(/已撤销/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /撤销$/ })).toBeNull();
  });

  it("says so when nothing has ever been shared", async () => {
    listShareLinks.mockResolvedValue({ links: [] });
    render(<ShareLinks />);
    expect(await screen.findByText(/还没有分享链接/)).toBeTruthy();
  });

  it("shows why the list failed instead of an empty panel", async () => {
    listShareLinks.mockRejectedValue(new Error("会话已过期"));
    render(<ShareLinks />);
    expect(await screen.findByText(/会话已过期/)).toBeTruthy();
  });
});
