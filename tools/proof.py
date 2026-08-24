#!/usr/bin/env python3
"""Proofing pass over the deliverable documents.

Checks the classes of defect that survive human proofreading and read as
carelessness: broken cross-references (most likely after renumbering), heading
number gaps, ragged tables, doubled words, and leftover placeholders.
"""
import collections
import re
import sys


def check(path):
    text = open(path).read()
    lines = text.split("\n")
    issues = []

    # Fenced blocks hold ASCII diagrams; prose rules do not apply inside them.
    fenced, fence = set(), False
    for i, l in enumerate(lines):
        if l.startswith("```"):
            fence = not fence
            fenced.add(i)
        elif fence:
            fenced.add(i)

    # --- headings: numbering sequential, no gaps or repeats ---------------
    h1 = [(i + 1, m.group(1), m.group(2))
          for i, l in enumerate(lines)
          if (m := re.match(r"^# (\d+)\.\s+(.*)", l))]
    nums = [int(n) for _, n, _ in h1]
    for a, b in zip(nums, nums[1:]):
        if b != a + 1:
            issues.append(("HEADING", f"section {a} followed by {b} — gap or repeat"))

    h2 = collections.defaultdict(list)
    for i, l in enumerate(lines):
        if m := re.match(r"^## (\d+)\.(\d+)\s", l):
            h2[int(m.group(1))].append((int(m.group(2)), i + 1))
    for sec, subs in h2.items():
        got = [s for s, _ in subs]
        if got != sorted(got):
            issues.append(("HEADING", f"§{sec} subsections out of order: {got}"))
        if len(set(got)) != len(got):
            issues.append(("HEADING", f"§{sec} has duplicate subsection numbers: {got}"))
        for a, b in zip(got, got[1:]):
            if b != a + 1:
                issues.append(("HEADING", f"§{sec}.{a} followed by §{sec}.{b} — gap"))

    # --- cross-references point at something that exists ------------------
    have_sec = set(nums)
    have_sub = {f"{s}.{n}" for s, subs in h2.items() for n, _ in subs}
    for i, l in enumerate(lines):
        if i in fenced:
            continue
        # A reference qualified by a filename points at another document.
        local = re.sub(r"`?[\w./-]+\.md`?[\s]*§\d+(?:\.\d+)?", "", l)
        for ref in re.findall(r"§(\d+(?:\.\d+)?)", local):
            ok = ref in have_sub if "." in ref else int(ref) in have_sec
            if not ok:
                issues.append(("XREF", f"line {i+1}: §{ref} does not exist"))
        # "Section 12" style references
        for ref in re.findall(r"\bSection (\d+)\b", local):
            if int(ref) not in have_sec:
                issues.append(("XREF", f"line {i+1}: Section {ref} does not exist"))

    # --- tables: every row has the header's column count -------------------
    i = 0
    while i < len(lines):
        if lines[i].startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            ncol = lines[i].count("|") - 1
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                if lines[j].count("|") - 1 != ncol:
                    issues.append(("TABLE", f"line {j+1}: {lines[j].count('|')-1} cells, header has {ncol}"))
                j += 1
            i = j
        else:
            i += 1

    # --- doubled words and leftover placeholders ---------------------------
    for i, l in enumerate(lines):
        if i in fenced:
            continue
        for m in re.finditer(r"\b(\w{3,})\s+\1\b", l, re.I):
            issues.append(("TYPO", f"line {i+1}: doubled word '{m.group(1)}'"))
        for ph in ("TODO", "TBD", "FIXME", "XXX", "Lorem", "<placeholder>"):
            if ph in l:
                issues.append(("PLACEHOLDER", f"line {i+1}: '{ph}'"))
        if re.search(r"\s+[.,;]", l) and "|" not in l:
            issues.append(("SPACING", f"line {i+1}: space before punctuation"))

    return issues


if __name__ == "__main__":
    total = 0
    for p in sys.argv[1:]:
        iss = check(p)
        total += len(iss)
        print(f"\n=== {p} — {len(iss)} issue(s) ===")
        by = collections.defaultdict(list)
        for kind, msg in iss:
            by[kind].append(msg)
        for kind in sorted(by):
            for m in by[kind][:14]:
                print(f"  {kind:12s} {m}")
            if len(by[kind]) > 14:
                print(f"  {kind:12s} ... and {len(by[kind])-14} more")
    print(f"\nTOTAL: {total}")
