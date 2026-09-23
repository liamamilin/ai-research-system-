"""Markdown → standalone HTML rendering for report export."""

from __future__ import annotations

import html

import markdown as md

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ max-width: 860px; margin: 2rem auto; padding: 0 1rem;
         font-family: -apple-system, "PingFang SC", "Segoe UI", sans-serif;
         line-height: 1.7; color: #1f2328; }}
  h1, h2, h3 {{ line-height: 1.3; margin-top: 1.6em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.92em; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  code {{ background: #f6f8fa; padding: 0.15em 0.35em; border-radius: 4px;
          font-size: 0.9em; }}
  pre {{ background: #f6f8fa; padding: 12px; border-radius: 6px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ border-left: 4px solid #d0d7de; margin: 1em 0; padding: 0 1em;
                color: #59636e; }}
  a {{ color: #0969da; }}
  @media print {{ body {{ margin: 0; max-width: none; }} }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_markdown(text: str) -> str:
    """Render Markdown to an HTML fragment (GFM tables, fenced code)."""
    return md.markdown(
        text or "",
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        output_format="html5",
    )


def render_report_html(title: str, text: str) -> str:
    """Render a full standalone HTML document for a report."""
    return _TEMPLATE.format(title=html.escape(title or "Report"),
                            body=render_markdown(text))
