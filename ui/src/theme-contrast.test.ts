/**
 * Contrast guards.
 *
 * Both of these were measured failures, not preferences: Lighthouse reported
 * the Reports page at 0.91 accessibility, and a DOM sweep found white text on
 * #3b82f6 at 3.68:1 (AA needs 4.5:1 for 14px) plus 22px and 16px touch targets
 * against a 24px minimum. The page is now at 1.00.
 *
 * They are cheap to undo by accident and invisible in review -- a colour swap
 * looks fine in a diff -- so the invariant is written down here.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const UI_ROOT = join(__dirname, "..");
const PROJECT_ROOT = join(UI_ROOT, "..");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

function relativeLuminance(hex: string): number {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const f = (v: number) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r!) + 0.7152 * f(g!) + 0.0722 * f(b!);
}

function contrast(fg: string, bg: string): number {
  const a = relativeLuminance(fg);
  const b = relativeLuminance(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

describe("theme contrast", () => {
  const config = readFileSync(join(UI_ROOT, "tailwind.config.js"), "utf-8");
  const css = readFileSync(join(UI_ROOT, "src", "index.css"), "utf-8");

  it("keeps a solid accent for surfaces carrying white text", () => {
    // #3b82f6 is 3.68:1 against white -- short of AA for 14px text -- but it
    // is 5.29:1 as link text on the page background, and #2563eb would drop
    // that to 3.76:1. One token cannot serve both, so there are two.
    // Scoped to the accent block: `DEFAULT:` appears for several colour scales.
    const accentBlock = config.match(/accent:\s*\{[\s\S]*?\n\s{8}\},/)?.[0] ?? "";
    expect(accentBlock, "the accent colour block was not found").toBeTruthy();

    const solid = accentBlock.match(/solid:\s*"(#[0-9a-f]{6})"/i)?.[1];
    expect(solid, "accent.solid is not defined").toBeTruthy();
    expect(contrast("#ffffff", solid!)).toBeGreaterThanOrEqual(4.5);

    const link = accentBlock.match(/DEFAULT:\s*"(#[0-9a-f]{6})"/i)?.[1];
    expect(link, "accent.DEFAULT is not defined").toBeTruthy();
    expect(contrast(link!, "#0b0d10")).toBeGreaterThanOrEqual(4.5);
  });

  it("uses the solid accent wherever white text sits on it", () => {
    const needle = ["bg-accent", "text-white"].join(" ");
    const offenders: string[] = [];
    for (const file of walk(join(UI_ROOT, "src"))) {
      // This file quotes the offending pair in order to search for it.
      if (file.endsWith("theme-contrast.test.ts")) continue;
      if (readFileSync(file, "utf-8").includes(needle)) {
        offenders.push(file.replace(PROJECT_ROOT, ""));
      }
    }
    expect(offenders, "white on bg-accent is 3.68:1 -- use bg-accent-solid").toEqual([]);
  });

  it("gives primary buttons the solid accent too", () => {
    expect(css).toMatch(/\.btn-primary\s*\{[^}]*bg-accent-solid/);
  });

  it("keeps badges and small controls at the 24px AA target size", () => {
    expect(css).toMatch(/\.badge\s*\{[^}]*min-h-\[24px\]/);
  });
});
