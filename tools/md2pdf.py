#!/usr/bin/env python3
"""Markdown -> print-ready PDF via headless Chrome.

Chrome rather than LaTeX because these documents contain Unicode box-drawing
diagrams (the architecture and dependency graphs). LaTeX would need font
plumbing to render those at all; a browser with DejaVu Sans Mono renders them
as authored.

Usage: md2pdf.py <file.md> [more.md ...]
"""
import html
import os
import re
import subprocess
import sys

import markdown

CSS = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm; }
@page { @bottom-center { content: counter(page); } }
body { font-family: "DejaVu Sans","Liberation Sans",sans-serif; font-size: 9.5pt;
       line-height: 1.45; color: #16181d; }
/* Sections flow continuously. A forced break per section left large areas of
   whitespace, which reads as padding rather than structure. Separation now comes
   from spacing and the rule under the heading; `page-break-after: avoid` still
   stops a heading being stranded at the foot of a page. */
h1 { font-size: 15pt; margin: 26px 0 10px; padding-bottom: 5px;
     border-bottom: 2px solid #16181d; page-break-after: avoid;
     break-after: avoid; }
h1:first-of-type { margin-top: 0; }
h2 { font-size: 12pt; margin: 18px 0 6px; color: #1b3a6b; page-break-after: avoid;
     break-after: avoid; }
h3 { font-size: 10.5pt; margin: 14px 0 4px; page-break-after: avoid; }
p { margin: 0 0 7px; orphans: 3; widows: 3; }
ul, ol { margin: 0 0 8px; padding-left: 20px; }
li { margin-bottom: 2px; }
strong { color: #000; }
code { font-family: "DejaVu Sans Mono",monospace; font-size: 8.2pt;
       background: #f2f3f5; padding: 0.5px 3px; border-radius: 2px; }
/* Diagrams must not wrap or they stop being diagrams. 7pt fits the widest
   (~80 columns) inside the A4 text block. */
pre { font-family: "DejaVu Sans Mono",monospace; font-size: 7pt; line-height: 1.25;
      background: #f7f8fa; border: 1px solid #dfe2e7; border-left: 3px solid #1b3a6b;
      padding: 7px 9px; overflow: visible; white-space: pre; page-break-inside: avoid;
      margin: 8px 0; }
pre code { background: none; padding: 0; font-size: inherit; }
/* auto, not fixed: fixed gives a one-character '#' column the same width as a
   prose column, which wasted a third of every table. auto sizes to content and
   overflow-wrap below still prevents long cells overflowing the page. */
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px;
        font-size: 8pt; table-layout: auto; page-break-inside: auto; }
/* `overflow-wrap: anywhere` also shrinks a column's intrinsic minimum width, so
   the browser was free to make columns narrower than their longest word and then
   hyphenate mid-syllable ("Deliverabl/e"). `break-word` leaves intrinsic sizing
   alone: a column is at least as wide as its longest word, and breaking only
   happens when a word genuinely cannot fit. */
th, td { border: 1px solid #c8ccd3; padding: 4px 5px; text-align: left;
         vertical-align: top; overflow-wrap: break-word; hyphens: none; }
th { background: #eef1f5; font-weight: bold; }
tr { page-break-inside: avoid; }
tbody tr:nth-child(even) { background: #fafbfc; }
blockquote { margin: 8px 0; padding: 6px 10px; background: #fff8e6;
             border-left: 3px solid #d9a441; }
hr { border: none; border-top: 1px solid #dfe2e7; margin: 14px 0; }
/* Without this an image renders at its intrinsic pixel width. A figure wider
   than the text column then overflows the page and Chrome pushes the rest of
   the content off it -- the PDF still builds, so the only symptom is a page
   that has gone blank. */
img { max-width: 100%; height: auto; display: block; margin: 10px auto;
      page-break-inside: avoid; }
a { color: #1b3a6b; text-decoration: none; }
"""


def convert(src):
    with open(src) as f:
        text = f.read()

    body = markdown.markdown(
        text, extensions=["tables", "fenced_code", "sane_lists", "attr_list"])

    # python-markdown leaves literal arrows/box glyphs alone, which is what we
    # want -- but it also escapes nothing inside <pre>, so diagrams survive.
    title = re.sub(r"^#\s*", "", text.split("\n")[0]).strip() or os.path.basename(src)
    # The temp HTML is written to a scratch directory, so relative asset paths
    # in the markdown (images, above all) would resolve against that directory
    # and silently fail to load -- Chrome renders the page regardless, so the
    # only symptom is a missing figure. A <base> pointing at the source file's
    # own directory makes relative paths mean what the author meant.
    base = "file://" + os.path.dirname(os.path.abspath(src)) + "/"

    page = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<base href='{base}'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>")

    tmp = f"/tmp/claude-1000/-home-jk-Documents/6ccf63f4-7ac2-4873-9424-978374156338/scratchpad/{os.path.basename(src)}.html"
    with open(tmp, "w") as f:
        f.write(page)

    out = os.path.splitext(src)[0] + ".pdf"
    r = subprocess.run([
        "google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=10000",
        f"--print-to-pdf={out}", f"file://{tmp}"],
        capture_output=True, text=True, timeout=180)
    ok = os.path.exists(out) and os.path.getsize(out) > 20000
    if not ok:
        print(f"  ! chrome: {r.stderr.strip()[:200]}")
    return out, ok


if __name__ == "__main__":
    for src in sys.argv[1:]:
        out, ok = convert(src)
        size = os.path.getsize(out) / 1024 if os.path.exists(out) else 0
        print(f"{'ok ' if ok else 'FAIL'}  {os.path.basename(out)}  ({size:.0f} KB)")
