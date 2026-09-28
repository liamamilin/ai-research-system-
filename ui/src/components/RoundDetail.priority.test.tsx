import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PriorityBadge } from "./RoundDetail";
import { normalizePriority } from "@/lib/utils";

afterEach(cleanup);

describe("normalizePriority", () => {
  it("reduces every shape the tracking database actually holds", () => {
    // Counts from state/tracking.db: 23x "**P0**", 28x "**P1**", 12x "**P2**"
    // against 6/7/3 clean ones, plus 39 empty.
    expect(normalizePriority("**P0**")).toBe("P0");
    expect(normalizePriority("**P1**")).toBe("P1");
    expect(normalizePriority("`P0`")).toBe("P0");
    expect(normalizePriority(" p2 ")).toBe("P2");
    expect(normalizePriority("p0")).toBe("P0");
    expect(normalizePriority("")).toBe("");
    expect(normalizePriority(null)).toBe("");
  });
});

describe("PriorityBadge", () => {
  it("styles a bolded P0 as urgent rather than falling back to P2", () => {
    // The regression: the style lookup is an exact match, so "**P0**" missed
    // every entry and rendered in PRIORITY_STYLE.P2 -- the muted grey of a
    // medium-priority item. 23 rows were lying about being top priority.
    const { container } = render(<PriorityBadge value="**P0**" />);
    const badge = container.querySelector("[data-priority]")!;
    expect(badge.getAttribute("data-priority")).toBe("P0");
    expect(badge.className).toContain("text-danger");
    expect(badge.className).not.toContain("text-text-muted");
    expect(badge.textContent).toBe("P0");
  });

  it("distinguishes the three real levels", () => {
    for (const [raw, expected] of [
      ["P0", "text-danger"],
      ["P1", "text-warning"],
      ["P2", "text-text-muted"],
    ] as const) {
      const { container, unmount } = render(<PriorityBadge value={raw} />);
      expect(container.querySelector("[data-priority]")!.className).toContain(expected);
      unmount();
    }
  });

  it("still falls back for an unrecognised level", () => {
    const { container } = render(<PriorityBadge value="**P9**" />);
    expect(container.querySelector("[data-priority]")!.className).toContain("text-text-muted");
  });

  it("renders nothing for an absent priority", () => {
    const { container } = render(<PriorityBadge value="" />);
    expect(container.querySelector("[data-priority]")).toBeNull();
    expect(screen.queryByText(/./)).toBeNull();
  });
});
