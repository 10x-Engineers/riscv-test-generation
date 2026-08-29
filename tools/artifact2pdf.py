#!/usr/bin/env python3
"""Artifact HTML -> print-ready PDF via headless Chrome.

Companion to md2pdf.py, for the pages published as Artifacts. Those files are
*fragments*: the publishing step wraps them in a document skeleton, so they carry
a `<title>` and a `<style>` but no `<html>`, `<head>` or `<body>`. Rendering one
locally therefore needs the skeleton put back before Chrome sees it.

Three things are added beyond the wrapper, each because the page was authored for
a screen:

  * **The light theme is forced.** The pages define their palette against
    `prefers-color-scheme`, and headless Chrome may resolve that to dark --
    producing a PDF with a near-black background that is correct on screen and
    useless on paper. Stamping `data-theme="light"` on the root selects the light
    token set the pages already define.

  * **Print geometry.** A4 with sensible margins, and the on-screen column cap
    lifted so the text fills the page rather than sitting in a narrow ribbon.

  * **Break control.** Figures, tables and the callout blocks are kept off page
    boundaries, and headings are not left stranded at the foot of a page.

Usage: artifact2pdf.py <artifact.html> [more.html ...]
"""
import os
import subprocess
import sys

PRINT_CSS = """
@page { size: A4; margin: 15mm 13mm 16mm 13mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { background: #fff !important; font-size: 9.6pt; line-height: 1.5; }

/* The screen layout caps the column for readability at desktop widths; on A4
   that cap would leave a third of the page empty. */
.wrap { max-width: none !important; padding: 0 !important; }
p, li, figcaption, .standfirst { max-width: none !important; }

header { padding-top: 0 !important; }
h1 { font-size: 19pt !important; }
h2 { font-size: 13pt !important; }
h3 { font-size: 10.5pt !important; }

/* One vertical rhythm for the whole document. Screen spacing is tuned for a
   scrolling column; on A4 the same values leave sections drifting apart and
   figures floating away from the text that introduces them. */
section { margin-top: 16pt !important; }
h3 { margin-top: 12pt !important; }
p { margin-bottom: 6pt !important; }
figure { margin: 8pt 0 10pt !important; }
figcaption { margin-top: 6pt !important; }
.scroll { margin: 8pt 0 10pt !important; }
.claim, .key, .risk { margin: 10pt 0 !important; padding: 8pt 11pt !important; }
.work { margin: 10pt 0 !important; }
.work ul { padding: 8pt 11pt 10pt 26pt !important; }
.work li { margin-bottom: 4pt !important; }
.measures > div { padding: 6pt 11pt !important; }

/* Keep short units whole, but let tall ones flow. Forcing every workstream
   block and every figure onto a single page left roughly a third of each page
   empty -- four blank pages across eleven, which reads as padding rather than
   as structure. Blocks that can exceed half a page break instead, with their
   header kept attached to whatever follows it. */
.claim, .key, .risk, .links, tr { break-inside: avoid; }

/* Keep the drawing whole, but let its caption flow. Welding the two meant a
   figure with a long caption needed more space than a page could offer, and
   broke to the next page whole -- leaving a third of the previous one empty.
   A caption that splits across a page costs nothing; a split diagram does. */
figure { break-inside: auto; }
figure svg { break-inside: avoid; }
.work { break-inside: auto; }
.work .hd { break-after: avoid; }
h1, h2, h3 { break-after: avoid; }
thead { display: table-header-group; }
li { break-inside: avoid; }

/* A diagram taller than half a page forces a break before it whatever else is
   set, so cap them at a size that lets body text share the page. */
figure svg { max-height: 80mm; }

/* Wide content scrolls on screen; on paper it has to fit. */
.scroll { overflow: visible !important; }
table { font-size: 8.4pt !important; }
th, td { padding: 4.5pt 6pt !important; }

svg { max-width: 100% !important; height: auto !important; }

/* Links are printed as their text; the URL is dead weight on paper except for
   the companion-page cards, which exist to be followed. */
a { color: inherit; text-decoration: none; }
.links a .t { color: #3b5bb5 !important; }
"""


def convert(src, out=None):
    frag = open(src).read()
    out = out or os.path.splitext(src)[0] + ".pdf"
    page = ('<!doctype html><html lang="en" data-theme="light"><head>'
            '<meta charset="utf-8">'
            f'{frag_head(frag)}'
            f'<style>{PRINT_CSS}</style></head><body>'
            f'{frag_body(frag)}</body></html>')

    tmp = os.path.join(
        os.environ.get("SCRATCH", "/tmp"), os.path.basename(src) + ".print.html")
    with open(tmp, "w") as f:
        f.write(page)

    r = subprocess.run([
        "google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        # The pages pull IBM Plex from Google Fonts; without a budget Chrome can
        # print before the faces arrive and fall back mid-document.
        "--virtual-time-budget=20000",
        f"--print-to-pdf={out}", f"file://{tmp}"],
        capture_output=True, text=True, timeout=240)
    ok = os.path.exists(out) and os.path.getsize(out) > 20000
    if not ok:
        print(f"  ! chrome: {r.stderr.strip()[:300]}")
    return out, ok


def frag_head(frag):
    """The <title> and <link>/<style> the fragment declares, hoisted into <head>."""
    head = []
    for tag in ("<title>", "<link "):
        i = frag.find(tag)
        if i < 0:
            continue
        j = frag.index(">", i) + 1
        if tag == "<title>":
            j = frag.index("</title>") + len("</title>")
        head.append(frag[i:j])
    i = frag.find("<style>")
    if i >= 0:
        head.append(frag[i:frag.index("</style>") + len("</style>")])
    return "".join(head)


def frag_body(frag):
    """Everything after the fragment's own head material."""
    i = frag.find("</style>")
    return frag[i + len("</style>"):] if i >= 0 else frag


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for src in sys.argv[1:]:
        out, ok = convert(src)
        kb = os.path.getsize(out) / 1024 if os.path.exists(out) else 0
        print(f"{'ok  ' if ok else 'FAIL'} {os.path.basename(out)}  ({kb:.0f} KB)")
