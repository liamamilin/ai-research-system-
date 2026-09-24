import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "index.css"), "utf-8");

/**
 * The rendered-Markdown styles are hand-written because
 * @tailwindcss/typography was never installed: every `prose` class on the
 * pages was a no-op, so answers and reports rendered with no heading
 * hierarchy, no list markers and bare inline code. These assertions keep the
 * replacement honest and stop anyone from switching back to `prose`.
 */
describe("markdown styles", () => {
  const block = CSS.slice(CSS.indexOf(".md-body"));

  it("styles headings with a real hierarchy", () => {
    for (const level of ["h1", "h2", "h3", "h4"]) {
      expect(block).toContain(`.md-body ${level}`);
    }
    expect(block).toMatch(/\.md-body h1\s*\{[^}]*@apply text-xl/);
    expect(block).toMatch(/\.md-body h2\s*\{[^}]*@apply text-lg/);
    expect(block).toMatch(/\.md-body h3\s*\{[^}]*@apply text-base/);
  });

  it("restores list markers that Tailwind's preflight removes", () => {
    expect(block).toMatch(/\.md-body ul\s*\{\s*list-style:\s*disc outside/);
    expect(block).toMatch(/\.md-body ol\s*\{\s*list-style:\s*decimal outside/);
    expect(block).toMatch(/\.md-body li \+ li\s*\{/);
  });

  it("gives inline code a background and code blocks no inline chrome", () => {
    expect(block).toMatch(/\.md-body code\s*\{[^}]*background-color|@apply[^}]*bg-bg-hover/);
    expect(block).toMatch(/\.md-body pre code\s*\{[^}]*border-0/);
  });

  it("limits the line length for answers and lets documents go wide", () => {
    expect(block).toMatch(/\.md-body\s*\{[^}]*max-width:\s*74ch/);
    expect(block).toMatch(/\.md-body-wide\s*\{\s*max-width:\s*none/);
  });

  it("keeps tables scrollable instead of stretching the measure", () => {
    expect(block).toMatch(/\.md-body table\s*\{[^}]*overflow-x:\s*auto/);
    expect(block).toContain(".md-body th");
  });

  it("uses the project's own colour tokens, not a foreign palette", () => {
    // text-foreground does not exist in this theme; a silent fallback to the
    // browser default is what made headings look unstyled in the first place.
    expect(block).not.toContain("text-foreground");
  });
});

describe("pages that render markdown", () => {
  const pages = ["Ask.tsx", "ReportView.tsx", "ShareView.tsx"];

  it.each(pages)("%s uses the shared md-body classes", (page) => {
    const source = readFileSync(resolve(__dirname, "pages", page), "utf-8");
    expect(source).toContain("md-body");
    expect(source).not.toContain("prose-invert");
  });
});
