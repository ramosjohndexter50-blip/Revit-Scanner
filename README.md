# Hamdan

A pyRevit extension for model-health auditing, clash review, and sheet/room numbering.

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

**Run Audit** first offers three scan modes:

- **Quick Scan**: warnings, naming/parameter completeness, model health, and duplicate checks; skips clashes and their CSV/BCF exports.
- **Clash Scan**: choose configured pairs or a discipline preset, then choose all levels or a single level band. Presets also ask for a clearance tolerance in millimetres. Elements crossing a selected level band are included.
- **Full Audit**: health checks plus the category-pair clash rules from your configuration, across all levels.

The progress bar shows the current stage and elapsed time. **Cancel** stops at the
next checkpoint between Revit API operations; a cancelled scan is not saved as
completed audit history. Native bounding-box prefilters restrict clash comparisons
to nearby candidates in the selected categories. Findings still require visual
review. The HTML marks checks excluded from the chosen mode as **Not scanned**.
Scores and trends are compared only against previous scans with the same mode,
level, discipline preset and configuration. The first run of a new scope starts
a new baseline.

Quick Scan's **Active-model Category Groups** table counts records in the open RVT.
An Electrical or Mechanical group does not mean another model is linked. The
**Category Breakdown** table lists the exact Revit categories, counts, and up to
five sample element names/IDs per category. Link instance counts are shown separately.

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

## Live Element List

Open **Hamdan > Elements > Element List** and leave the window open. Clicking
elements in Revit adds named rows automatically: family, type, category, level,
and Element ID. A window such as `W-Aluminum_Fixed_Exterior` is shown with its
type, for example `1200 x 3180`. Repeated clicks update the same row instead of
adding duplicates. The list is kept while the window is open, with a 2,000-row limit.

Search by name or ID, pause **Live capture**, or use **Select & Zoom** on selected
rows. **Remove from list** and **Clear list** only affect this window's rows. The
window belongs to the model it was opened for; locating elements requires that
model to be active. Deleted elements are skipped. Family/type labels are also
included in Review Issues and the next generated HTML audit report.

## Custom sheet and room numbering

Open **Hamdan > Sheets > Number Formatter** (replaces the old Prefix Sheet Numbers
label). Choose **Sheets** or **Rooms**, select the items to number, then edit the
pattern in the live preview:

| Pattern | Floor | Tower | Start / Increment | Output |
| --- | --- | --- | --- | --- |
| `{floor}{tower}-{n:02}` | G | A | 3 / 1 | GA-03, GA-04, GA-05 |
| `{n:02}-{floor}{tower}` | G | A | 3 / 1 | 03-GA, 04-GA, 05-GA |
| `{tower}-{n:03}` | blank | B | 10 / 2 | B-010, B-012, B-014 |
| `A-{old}` | blank | blank | any / 1 | Add A- to existing numbers |

Codes are entered manually. `{name}` inserts the current sheet or room name. Set
the assignment order to current number (natural order), name, or Element ID. A
negative increment counts down, provided the sequence stays zero or greater.

**Apply** is enabled only when the preview is valid and has changes. The tool
blocks duplicate generated values, collisions with unselected numbers, and
read-only fields. Sheet numbers must be unique; room conflict checks are grouped
by created phase. Existing selected values can be swapped using temporary numbers
inside one transaction. A failed assignment rolls back the transaction. **Cancel**
closes the preview without changing the project. Revit validates any additional
restrictions on the generated text when applying the numbers.

## Important limitations

- Clash findings are bounding-box coordination candidates; verify them visually before resolution.
- The tool supports BIM coordination and QA evidence, but it does not certify BIM Level 3 or replace formal Navisworks/ACC model coordination.
- Use a copy of a project when validating any new workflow.
