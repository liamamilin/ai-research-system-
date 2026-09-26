"""Markdown → standalone HTML rendering for report export.

A report body is model output conditioned on web pages, so it is untrusted
input: python-markdown passes raw HTML straight through by design, and this
document is served from the app's own origin. Everything the renderer emits is
therefore sanitized before templating.
"""

from __future__ import annotations

import html

import markdown as md
import nh3

# Enough to render a research report: structure, tables, code, links, images.
# Notably absent: script, style, iframe, object, embed, form.
_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup",
    "dd", "del", "details", "div", "dl", "dt", "em", "figcaption", "figure",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li",
    "mark", "ol", "p", "pre", "q", "s", "samp", "section", "small", "span",
    "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "u", "ul", "var",
}
_ALLOWED_ATTRS = {
    "*": {"id", "class", "title"},
    # No "rel" here: link_rel below sets it on every link, and nh3 rejects both
    # being configured at once.
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align", "scope"},
    "ol": {"start"},
    "details": {"open"},
}
# Web links and embedded images only: no javascript:, data: or file: URLs.
_ALLOWED_SCHEMES = {"http", "https", "mailto", "ftp"}

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


def sanitize_html(fragment: str) -> str:
    """Strip anything executable from an HTML fragment.

    An allowlist, not a blocklist: the input is model output, so unknown tags
    are removed rather than a hand-maintained list of "bad" ones being trusted
    to stay complete.
    """
    return nh3.clean(
        fragment or "",
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_SCHEMES,
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


def render_markdown(text: str) -> str:
    """Render Markdown to a sanitized HTML fragment (GFM tables, fenced code)."""
    return sanitize_html(md.markdown(
        text or "",
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        output_format="html5",
    ))


def render_report_html(title: str, text: str) -> str:
    """Render a full standalone HTML document for a report."""
    return _TEMPLATE.format(title=html.escape(title or "Report"),
                            body=render_markdown(text))
