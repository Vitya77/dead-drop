# SRS export and validation

The source of truth is [srs.md](srs.md). The submitted artifact is [dead-drop-srs.pdf](../output/pdf/dead-drop-srs.pdf). Diagram sources are the Graphviz files in `diagrams/`; the exporter generates their shared Markdown/PDF PNG assets in `assets/`.

## Prerequisites

Use Python 3.10 or newer with `reportlab` and `pypdf`, and Graphviz's `dot` executable on PATH. Visual review also needs Poppler's `pdftoppm` and an image viewer. These are documentation tooling dependencies, not application stack constraints. They do not require the application to be implemented.

Install missing Python packages in your preferred virtual environment with `python -m pip install reportlab pypdf`; install Graphviz and Poppler through your operating system package manager. The exporter performs no automatic downloads and uses no network services.

## Rebuild

From the repository root, run `python docs/export_srs.py`. In environments requiring RTK, use `rtk proxy python docs/export_srs.py`.

The command regenerates three diagram PNGs and the PDF, checks all local links and heading anchors, verifies 30 functional and 15 non-functional requirements, checks required fields and complete traceability, and compares every rendered text block/cell against extracted PDF text. It also verifies embedded diagrams and external-reference URLs. A failure exits nonzero.

The renderer deliberately supports the Markdown used in this SRS: headings, paragraphs, flat bullets, tables, standalone images, bold text, inline code, and links. Keep future edits within that subset or extend the exporter explicitly. Requirement and scenario counts are asserted; update the stated inventory and validator together if the baseline grows. PDF page furniture includes the baseline version/date and must be updated with document metadata when preparing another revision.

`python docs/export_srs.py --check` validates source structure, anchors, matrix coverage, and the existing PDF requirement inventory without rewriting artifacts. It does not prove that the existing PDF contains the latest source text; run the full exporter after every source or diagram change.

## Visual review

Render every page with `pdftoppm -r 110 -png output/pdf/dead-drop-srs.pdf /tmp/dead-drop-srs-page`. Inspect every image, using a contact sheet for overview and full-size inspection for dense tables and diagrams. Check headings, paragraph boundaries, table continuation headers, page numbers, clipping, and diagram labels. Re-export and repeat after a layout-affecting edit. Keep review images outside the repository.

Review the specification itself for agreement among the role matrix, requirements, lifecycle diagram, truth tables, use cases, and verification scenarios. Confirm that the README links still resolve and run `git diff --check`.

## Evidence boundary

Export success validates documentation artifacts only. The application scenarios in Chapter 8 remain **Planned / Not collected**. Do not replace them with passed results based on PDF generation, source inspection, or diagram rendering.

## Baseline artifact review: 2026-10-20

The version 0.1 export was checked as follows:

* Source validation passed: nine numbered chapters, 30 functional requirements, 15 non-functional requirements, 20 planned scenarios, unique IDs, required fields, complete matrix coverage, and resolving local links/anchors.
* Full export validation passed: all rendered text blocks and table cells are present in the PDF; external-reference URLs and three shared diagram images are retained.
* All 36 PDF pages were rendered and visually inspected. Dense traceability/lifecycle tables and the component diagram were also inspected at full page resolution. Requirement blocks remain together, continuation tables repeat their headers, and no clipping or overlap was found.
* PDF character-bound checks found no text outside the intended page margins. Documentation source whitespace/final-newline checks and `git diff --check` passed.
* Permissions, lifecycle transitions, ALL/ANY tables, warning timing, suspension, deletion, and recovery scenarios were reviewed for consistency with the requirements. This is a documentation review, not evidence that the application behaves this way.

Regenerate and repeat the checks after changing the SRS or diagram sources; this review applies to the delivered version 0.1 artifacts.
