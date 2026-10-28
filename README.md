# Hamdan

A pyRevit extension for model-health auditing, clash review, and sheet/room numbering.

## What it checks

- Native Revit warnings, grouped and severity-ranked
- Confirmed hard clashes from positive-volume solid intersections, plus separately labelled bounding-box clearance candidates
- Host and loaded linked-model geometry, with nested link transforms and source identity
- A self-contained HTML dashboard that opens after each scan, plus JSON results for the current scan
- Clash states: New, Active, Resolved, and Approved
- CSV and BCF 2.1 clash exports
- Naming rules, required-parameter completeness, workset health, link-coordination review and performance flags
- Top ten fixes ranked by estimated Health Score impact
- Per-level and per-discipline breakdowns
- Review Issues: searchable findings with severity, Element IDs, level, and suggested fixes; select and zoom to an issue directly in Revit

The audit is read-only: it creates reports but never adds, edits, or deletes Revit model elements.
**Create Sheets** and **Number Formatter** are separate editing commands with a preview and explicit Apply/Create.

## Install in pyRevit

1. In Revit, open **pyRevit > Extensions > Extension Manager**.
2. Paste this repository URL into **Git URL**:

   `https://github.com/ramosjohndexter50-blip/Revit-Scanner.git`

3. Leave the install path at pyRevit's default, then choose **Add and install**.
4. Reload pyRevit or restart Revit.
5. Open **Hamdan > Audit > Run Audit**.

## Reports and configuration

**Run Audit** first offers three scan modes:

- **Quick Scan**: warnings, naming/parameter completeness, model health; skips clashes and their CSV/BCF exports.
- **Clash Scan**: choose configured pairs or a discipline preset, then choose all levels or a single level band. Presets also ask for a clearance tolerance in millimetres. Elements crossing a selected level band are included.
- **Full Audit**: health checks plus the category-pair clash rules from your configuration, across all levels.

The progress bar shows the current stage and elapsed time. **Cancel** stops at the
next checkpoint between Revit API operations; a cancelled scan is not saved as
a completed result. Native bounding-box prefilters restrict clash comparisons
to nearby candidates in the selected categories, including transformed link instances.
Hard clashes require positive solid-intersection volume. Clearance findings use bounding-box
distance as a conservative candidate check; they do not certify actual surface clearance.
The HTML marks checks excluded from the chosen mode as **Not scanned**.
Custom duplicate-element and repeated-Mark checks are excluded from all scan modes,
findings, recommendations and scores. Repeated content may be intentional. Native
Revit warnings are still reported as returned by Revit, including duplicate-related warnings.
Existing duplicate tolerance/weight keys are accepted for configuration compatibility and have no effect.
Every scan starts fresh. No previous report, score comparison, approval or assignment is
loaded. Starting an audit or a document check clears the prior current result for that
open document; cancellation/failure leaves no old result to fall back to. Open Dashboard
and Review Issues show only the latest successful scan in this opening of the model.
Closing/reopening the same RVT, opening a different model or Save As starts without a
current result. Archived HTML/JSON/CSV/BCF files remain on disk for manual access and are
not automatically restored. Existing browser tabs remain snapshots of their original scan.
BEP profiles and audit rules remain saved; this reset applies to scan results and tracking.

The dashboard lists check status, time per check, geometry coverage and up to 500 detailed
diagnostics with total clash-coverage counters. Failed checks, closed worksets, missing
geometry or unloaded clash sources produce **Incomplete**, rather than a numeric passing
score. Clearance candidates and informational workset/link
messages do not reduce the score. The same penalty rows drive both the score and Fix First.
Edit `score_thresholds` and `health_weights` for the agreed project rules. Naming rules are
project-configurable checks, not an ISO 19650 certification.

Quick Scan's **Active-model Category Groups** table counts records in the open RVT.
An Electrical or Mechanical group does not mean another model is linked. The
**Category Breakdown** table lists the exact Revit categories, counts, and up to
five sample element names/IDs per category. Link instance counts are shown separately.

The first scan creates this local folder and opens the timestamped HTML dashboard in your default browser:

`Documents/RevitScanner/reports`

It also writes `audit_config.json` there. Edit that file to adjust hard-clash category pairs, clearance tolerances, ignore rules, naming regexes, required parameters, and Health Score weights. The HTML dashboard links to the JSON results, CSV clashes, and BCF package for that audit run.

## Review and locate findings

Run **Hamdan > Audit > Run Audit** once after updating. Then open **Review Issues**.
Select a row to read its suggested fix in the details panel, or search for a severity,
level, description, or Element ID. Click **Select & Zoom** to close the picker and
locate the issue in Revit. A view or sheet finding opens that view directly.

The reviewer uses only the current scan for the active, currently open document. It resolves
saved element UniqueIds before selecting; deleted elements are skipped. Unsaved
projects require a fresh audit after reopening Revit. Project/configuration checks
without element references remain reviewable but cannot be zoomed to. This action
changes only selection and the active view, with no edits to model geometry or parameters.

## Live Element List

Open **Hamdan > Elements > Element List** and leave the window open. Clicking
elements in Revit adds named rows automatically: family, type, category, level,
and Element ID. A window such as `W-Aluminum_Fixed_Exterior` is shown with its
type, for example `1200 x 3180`. Repeated clicks update the same row instead of
adding duplicates. The list is kept while the window is open.

Click **Add All Model Elements** to collect categorized host-model instances without
selecting the model in Revit. There is no fixed 2,000-row cap. Element types, view-specific
annotations and the contents of linked models are excluded. The collection is cancellable;
Cancel preserves the previous list. Virtualized rows limit UI rendering work, although the
records still use memory. **Select All Rows** selects all currently shown/search-matching
rows in the list; **Select & Zoom** then sends that selection to Revit.

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

## Create sheets

Open **Hamdan > Sheets > Create Sheets**. Choose a loaded title-block type (or **No title block**),
count, start, increment, floor/tower codes and patterns for numbers and names. For example,
number `{floor}{tower}-{n:02}`, name `General Arrangement {n:02}`, start `3` creates `GA-03`.
Use `{n:02}-{floor}{tower}` for number-first output. The preview blocks duplicate numbers
and collisions with existing sheets, including placeholders. Click **Create** to apply the
batch in one Revit transaction. Failed creation/assignment rolls back the batch.
The batch limit is 500 sheets; it does not place views automatically.

## Productivity and document checks (3.1)

- **Sheets > Sheet Manager:** filter the sheet table, tick Use, edit Number/Name,
  additional revision IDs and one optional sheet text parameter. Review changes lists
  every old/new value before Apply. Sheet-number swaps are supported. Additional
  revisions do not remove cloud-driven revisions or edit project revision definitions.
- **Elements > Batch Parameters:** choose current selection, active view or whole model,
  then categories, instance/type scope and an exact parameter identity. Optional prefill
  sets a common proposed value. Select rows and review before Apply. Type edits affect
  every instance of that type. Double values use project display units; integers require
  whole numbers (Yes/No uses 1/0). Element-reference parameters are excluded. Use Sheet
  Manager for sheet Number/Name. Read-only/missing targets are counted and excluded.
- **Audit > Dimensions & Tags:** choose host views. Checks direct/segment dimension
  ValueOverride, orphaned IndependentTags and missing local independent tags for the
  configured categories. Default required-tag categories: Doors and Windows. Missing
  tags are BEP review candidates; the view collector is not proof that a tag is required.
  Linked tags and room/space tags are outside this check. This tool does not infer
  which geometry needs dimensions.
- **Sheets > Sheet Layout:** choose sheets. Tests viewport/schedule rectangles against
  a unique title-block bounding rectangle, pairwise layout overlap candidates and
  required title-block information. Includes schedules, excludes revision schedules and
  viewport labels. No/ambiguous title-block bounds leave outside-sheet coverage
  incomplete. Intended overlay views and family bounds need visual review.
- **Settings > BEP Profiles:** save, edit JSON, import/export and activate named profiles
  per model. Profiles own each supplied section; an empty section deliberately disables
  its rules. Invalid profiles stop the scan rather than silently applying defaults. A
  shared profile edit affects all models that use it. The profile name and full rule
  signature are saved with the scan for traceability. Each new scan starts fresh.
- **Audit > Review Issues:** select/zoom, edit workflow status, assignee, due date,
  comment and approval/resolution reason, or open the current issue dashboard. Only the current scan is shown; document checks replace the prior model-audit result.
  Tracking is local JSON scoped to that scan;
  it does not message assignees. Workflow edits record user/time and reject stale changes within that scan.
  Workflow Resolved does not prove a clash vanished; rerun the same clash scope.
  Approvals retain their evidence in the current issue register. A new scan discards
  earlier workflow decisions and independently evaluates the current geometry. The saved
  audit score is a snapshot; workflow decisions do not rewrite it.

Document checks create self-contained HTML plus UTF-8 CSV and JSON in the report
folder. Review Issues locates their element IDs inside Revit. A normal browser cannot
select Revit objects. Workflow dashboard changes are visible immediately when reopened;
saved audit HTML is a snapshot. Another scan creates a separate, fresh report.

All model edits use one atomic transaction with stale-preview validation. Cancel before
Apply changes no model content. Unchecked rows are not written. Worksharing permissions
and Revit validation can reject a batch; the tool rolls it back rather than reporting
partial success. The new Scan Coverage panel distinguishes failed/incomplete checks,
skipped geometry events and loaded links whose documents cannot be read.

## Dimension and tag editing (3.2)

Open **Hamdan > Audit > Dimensions & Tags**. The first menu keeps **Scan dimensions & tags**
and adds three edit actions. Scope can be current selection, active view or chosen views.
The table supports search, **Select all shown**, per-row Use, and exact old/new preview.
Only changed Use rows are committed after **Review changes > Apply changes**.

- **Edit Dimensions:** prefix, suffix, above, below and ValueOverride for individual dimensions
  or chain segments. Measured value is read-only and formatted in project units from the
  numeric measurement, without prefix/suffix/override. Clearing ValueOverride restores the
  measurement. This editor changes annotation text; geometry and measured length are unchanged.
- **Edit Tags:** compatible loaded tag type, leader Yes/No, Horizontal/Vertical orientation,
  and relative movement right/up in owner-view directions. Movement is in model millimetres,
  not paper millimetres; negative values move left/down. A replacement type is offered only
  when compatible with every tag in the chosen scope and applies only to Use rows.
- **Edit Tagged Values:** resolves and deduplicates local elements referenced by the chosen
  IndependentTags, then opens Batch Parameters for those elements. Choose the exact instance
  or type parameter used by your tag family label. Example: a wall tag displaying Type Mark
  needs a type parameter edit; changing instance Mark will not update that label. All tags
  displaying the changed parameter update, and a type edit affects all instances of that type.

Spot dimensions, pinned/grouped dimensions, pinned/grouped/orphaned tags and tags managed by
multi-reference annotations are excluded from the appearance editor with a skipped reason.
Room/space/area tags are not IndependentTags and are outside this editor. Linked tag label
values must be edited in their source document; local tag appearance may be edited where
Revit permits. Leader endpoints/elbows, custom family label definitions and dimension type
styles are outside this version. Read-only/worksharing restrictions can reject a batch.
Stale values, removed targets or an invalid write reject/roll back the entire batch.
Cancel before Apply leaves the model unchanged. Normal Revit Undo handles successful batches.

## Development and verification

The installed extension has no third-party Python dependency. Development-only XSD tests
use `lxml`: install `requirements-dev.txt`, then run `python -m unittest discover -s tests -v`.
GitHub Actions runs the portable regressions on Python 3.10 and 3.12. The BCF tests validate
every topic, viewpoint and version file against the official buildingSMART 2.1 schemas.
Runtime source stays compatible with IronPython 2.7 syntax. The modeless Element List uses
pyRevit's IronPython event support; CPython modeless support is not claimed.

See [VALIDATION.md](VALIDATION.md) for the actual verification boundary and Revit smoke-test cases.

## Important limitations

- Hard clashes use solid intersections; meshes or solids that cannot be read/intersected are logged as incomplete coverage.
- Reflected link transforms that the Revit solid API rejects are logged rather than reported clash-free.
- BCF cameras use host internal coordinates converted to metres. Revit component IDs are exported as AuthoringToolId; IFC GUIDs are not fabricated. A BCF viewer may need its own Revit/link-to-IFC mapping to highlight components.
- Family file-size, nested-family depth and purgeability are not measured by this scan; the report discloses this.
- The tool supports BIM coordination and QA evidence, but it does not certify BIM Level 3 or replace formal Navisworks/ACC model coordination.
- Use a copy of a project when validating any new workflow.
