# Hamdan

A standalone, scan-only pyRevit extension for model-health and clash auditing.

## What it checks

- Native Revit warnings, grouped and severity-ranked
- Hard and clearance clash candidates with editable category-pair tolerances
- A self-contained HTML dashboard that opens after each scan, plus JSON audit history, warning/clash trends, and Health Score delta
- Clash states: New, Active, Resolved, and Approved
- CSV and BCF 2.1 clash exports
- Naming rules, required-parameter completeness, workset health, link-coordination review, possible duplicates, and performance flags
- Top ten fixes ranked by estimated Health Score impact
- Per-level and per-discipline breakdowns
- Review Issues: searchable findings with severity, Element IDs, level, and suggested fixes; select and zoom to an issue directly in Revit

The audit is read-only: it creates reports but never adds, edits, or deletes Revit model elements.

## Install in pyRevit

1. In Revit, open **pyRevit > Extensions > Extension Manager**.
2. Paste this repository URL into **Git URL**:

   `https://github.com/ramosjohndexter50-blip/Revit-Scanner.git`

3. Leave the install path at pyRevit's default, then choose **Add and install**.
4. Reload pyRevit or restart Revit.
5. Open **Hamdan > Audit > Run Audit**.

## Reports and configuration

The first scan creates this local folder and opens the timestamped HTML dashboard in your default browser:

`Documents/RevitScanner/reports`

It also writes `audit_config.json` there. Edit that file to adjust hard-clash category pairs, clearance tolerances, ignore rules, naming regexes, required parameters, and Health Score weights. The HTML dashboard links to the JSON history, CSV clashes, and BCF package for that audit run.

## Review and locate findings

Run **Hamdan > Audit > Run Audit** once after updating. Then open **Review Issues**.
Select a row to read its suggested fix in the details panel, or search for a severity,
level, description, or Element ID. Click **Select & Zoom** to close the picker and
locate the issue in Revit. A view or sheet finding opens that view directly.

The reviewer uses the latest compatible report for the active model. It resolves
saved element UniqueIds before selecting; deleted elements are skipped. Unsaved
projects require a fresh audit after reopening Revit. Project/configuration checks
without element references remain reviewable but cannot be zoomed to. This action
changes only selection and the active view, with no edits to model geometry or parameters.

## Important limitations

- Clash findings are bounding-box coordination candidates; verify them visually before resolution.
- The tool supports BIM coordination and QA evidence, but it does not certify BIM Level 3 or replace formal Navisworks/ACC model coordination.
- Use a copy of a project when validating any new workflow.
