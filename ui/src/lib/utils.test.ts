import { describe, expect, it } from "vitest";

import { cn, formatTokens, timeAgo } from "./utils";

describe("cn", () => {
  it("merges conflicting tailwind classes", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
  });

  it("drops falsy values", () => {
    const hidden = false;
    expect(cn("a", hidden && "b", undefined, null, "c")).toBe("a c");
  });
});

describe("formatTokens", () => {
  it("handles null and undefined", () => {
    expect(formatTokens(null)).toBe("-");
    expect(formatTokens(undefined)).toBe("-");
  });

  it("formats raw, thousands and millions", () => {
    expect(formatTokens(999)).toBe("999");
    expect(formatTokens(1234)).toBe("1.2k");
    expect(formatTokens(2_500_000)).toBe("2.50M");
  });

  it("rounds boundaries at 1k and 1M", () => {
    expect(formatTokens(1000)).toBe("1.0k");
    expect(formatTokens(1_000_000)).toBe("1.00M");
  });
});

describe("timeAgo", () => {
  const ago = (ms: number) =>
    new Date(Date.now() - ms).toISOString().replace("Z", "+0000");

  it("returns empty string for missing value", () => {
    expect(timeAgo(null)).toBe("");
    expect(timeAgo(undefined)).toBe("");
  });

  it("parses backend timestamps with +0800 offset", () => {
    expect(timeAgo(ago(5 * 60_000))).toBe("5 分钟前");
  });

  it("handles hours and days", () => {
    expect(timeAgo(ago(3 * 3_600_000))).toBe("3 小时前");
    expect(timeAgo(ago(2 * 86_400_000))).toBe("2 天前");
  });

  it("shows 刚刚 for just now", () => {
    expect(timeAgo(ago(5_000))).toBe("刚刚");
  });

  it("falls back to the raw string on invalid dates", () => {
    expect(timeAgo("not-a-date")).toBe("not-a-date");
  });
});
