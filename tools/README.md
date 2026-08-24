# Document tooling

The proposal and plan are written in Markdown under `documentation/`. Everything
else — PDF, Word, the briefing deck — is **generated from those sources and not
committed**, for the same reason the compiled model IR should not be: a derived
artefact in version control drifts silently from what it was built from.

| Script | Purpose |
|---|---|
| `md2pdf.py <file.md>` | Print-ready PDF via headless Chrome. Chosen over LaTeX because the plans contain Unicode box-drawing diagrams that LaTeX cannot render without font work. |
| `mk_docx.py <in.md> <out.docx>` | Word/Google Docs version, keeping headings, tables and lists editable. |
| `proof.py <file.md>...` | Consistency check: heading numbering, cross-references that resolve, ragged tables, doubled words, leftover placeholders. Skips fenced diagram blocks and filename-qualified references to other documents. |

Run `proof.py` before regenerating anything for circulation.
