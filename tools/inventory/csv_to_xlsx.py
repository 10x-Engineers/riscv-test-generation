#!/usr/bin/env python3
"""Combine the inventory CSVs into one .xlsx, one sheet per category.

Nine separate CSVs are awkward to hand to anyone: they have to be uploaded and
opened one at a time, and a spreadsheet tool shows no relationship between them.
A single workbook uploads once, opens natively in Google Sheets and Excel, and
keeps the categories side by side as tabs.

Usage: csv_to_xlsx.py <csv-dir> [out.xlsx]
"""
import csv
import glob
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Sheet order follows the inventory's own narrative rather than the alphabetical
# order glob returns: what the architecture offers, then how it is reached, then
# the unified target list the denominator is built from.
ORDER = ["extensions", "instructions", "csrs", "exceptions", "interrupts",
         "privilege_modes", "translation_modes", "pmp_pma", "targets"]

HEADER_FILL = PatternFill("solid", fgColor="1B3A6B")
HEADER_FONT = Font(color="FFFFFF", bold=True)
MAX_WIDTH = 60  # keep a long `support_predicate` from swallowing the screen


def add_sheet(wb, name, path, first):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return
    ws = wb.active if first else wb.create_sheet()
    # Excel sheet names cap at 31 chars and forbid several characters; the
    # inventory's names are short and safe, but truncate defensively.
    ws.title = name[:31]

    for r in rows:
        ws.append(r)

    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")

    # Freeze the header and enable filtering: with 1255 target rows the sheet is
    # only useful if it can be sorted and filtered without losing the columns.
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for i, col in enumerate(zip(*rows), start=1):
        width = max((len(str(v)) for v in col), default=10)
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, MAX_WIDTH)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = os.path.abspath(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(src, "inventory.xlsx")

    found = {os.path.splitext(os.path.basename(p))[0]: p
             for p in glob.glob(os.path.join(src, "*.csv"))}
    if not found:
        sys.exit(f"no CSVs in {src}")

    ordered = [n for n in ORDER if n in found]
    ordered += sorted(n for n in found if n not in ORDER)

    wb = Workbook()
    for i, name in enumerate(ordered):
        add_sheet(wb, name, found[name], first=(i == 0))
    wb.save(out)

    print(f"wrote {out}")
    for name in ordered:
        with open(found[name], newline="") as f:
            n = sum(1 for _ in f) - 1
        print(f"  {name:20s} {n:5d} rows")


if __name__ == "__main__":
    main()
