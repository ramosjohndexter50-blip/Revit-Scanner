# Verification boundary

## Ribbon organization, 2026-10-30

Hamdan now contains exactly three panels, ordered Sheet, Model and QA. All twelve
commands appear once with their script, bundle metadata and icon present. Command
files retain their previous contents; only parent locations and panel ordering change.
The 92 portable regressions pass using the new paths, and all seven WPF layouts load.
The installed ribbon files are synchronized; displaying the new panels requires a
pyRevit reload. No Revit model edits or live audit were performed for this layout change.

## Custom duplicate checks removed, 2026-10-28

92 portable regressions pass. The scan no longer runs geometry/location duplicate
comparisons or repeated-Mark inventory checks. Tests exercise the scan pipeline,
legacy config acceptance, absent duplicate findings/penalties, and the generated HTML.
Native Revit duplicate warnings remain visible and retain their configured severity.
All 33 runtime Python files compile in the installed IronPython 2.7 engine.
This change has no new live Revit model scan or performance benchmark.

## Fresh scan sessions, 2026-10-28 (3.3)

92 portable regressions pass; 33 runtime Python files compile in the installed IronPython
2.7 engine. Fresh-scan cases cover close/reopen with identical path/hash, different live
managed wrappers for one native document, model switching/Save As, invalidation at scan
start, stale publish rejection, per-run issue registers and truthful fresh HTML. Actual
scan-mode tests fail if a previous audit or issue register is read.

Six native checks pass in isolated Revit 2025.5: two consecutive real Quick Scans (nine
checks and 41 findings each); prior approval/assignee/history exclusion on rescan; current
result retrieval from a different command engine and model isolation; started/unfinished
scan invalidation; a real dimension/tag document check replacing the earlier model scan;
and close/reopen of the identical RVT leaving no current report. Archived exports remain.
No Revit model was modified or saved by these tests.

The first native pass exposed that ReferenceEquals rejects different managed wrappers
for the same Revit document; the final implementation uses Revit Equals plus closed-object
invalidation and passed cross-command/reopen checks. This behavior replaces automatic
cross-run baselines and issue carryover described in older release notes below.

## Annotation editing, 2026-10-28 (3.2)

84 portable regression tests pass. Eight annotation-edit cases cover segment selection,
clear override without resizing, stale measurement/head rejection, pinned/grouped targets,
invalid input, incompatible types/orphaned tags, owner-view movement and transaction rejection.
All 32 runtime Python files compile in the installed pyRevit IronPython 2.7 engine; all seven
shared WPF layouts load.

Seven isolated native Revit 2025.5 checks pass: single dimension text/override restoration,
measured-value preservation and stale rejection; second-segment-only edits; independent-tag
leader/orientation/position; tagged wall Type Mark write with actual tag text readback;
readback of a reviewed UI dimension edit; compatible tag type change and readable type names;
and numeric measurement formatting without annotation text. The Dimension UI selected all
three rows, previewed only the one changed Below field (Native test -> UI verified), applied
one row and preserved its two-foot measurement. The Tag UI opened two scoped rows. Edit Tagged
Values resolved those tags to Walls and reached the existing parameter-category picker.

The original production project was not modified or saved. Tests ran only in Hamdan-QA-Host.
The QA model uses linear detail-curve dimensions and a wall tag family. Other dimension styles,
leader family constraints, linked appearance edits, worksharing denial, multi-leader behavior,
large batches and Revit 2024/2026 still need representative live validation. Room/space/area
and multi-reference annotation tags are deliberately excluded. This release adds no claim
about the previously unresolved linked-solid scan or 2.5 GB throughput.

## Productivity additions, 2026-10-28 (3.1)

76 portable regression tests pass, including profile isolation/validation, preserved
malformed JSON, busy-register rejection, issue reason/date/history and stale revisions,
approval reopening, sheet-number swaps/collisions, failed-write rollback, stale parameter
previews, annotation segments/tag coverage, paper-space tolerance and escaped HTML/CSV.
All 30 runtime Python sources compile in the installed IronPython 2.7 engine; all seven
XAML layouts load in native WPF.

The isolated Revit 2025.5 productivity fixture passes 6/6 native checks:
sheet-number swap; string parameter write and stale rejection; a real detail-curve
dimension with ValueOverride plus missing-tag candidates; real overlapping/out-of-bounds
viewports and missing title blocks; profile atomic JSON storage; issue tracking history.
The first dimension fixture used unsuitable references, then a location outside the
view crop; the final fixture uses geometric detail-curve references inside the view.
Quick Scan completed in native Revit after correcting a missing timing import, generated the new coverage dashboard and recorded nine completed checks.
The Sheet Manager UI also displayed seven sheets, selected all shown rows, edited one
QA sheet name, displayed its exact old/new preview and committed one changed row. Review Issues saved a QA coordinator assignment through
the native form and opened an HTML register containing the new assignment/history.
The Dimensions & Tags command also ran end-to-end for Level 0, exported HTML/CSV/JSON
and saved the dimension UniqueId in its issue references.

These are scoped fixture checks, not proof of all revision/title-block families or
numeric/type parameter combinations in production. Issued/cloud-driven revisions,
worksharing write denial, large selections, UI Undo and Revit 2024/2026 still need
representative live validation. The earlier linked-solid validation gap remains below.

The development regression suite validates portable algorithms and mocked Revit workflows.
It is not proof of live-model accuracy, a 2.5 GB performance benchmark or universal BEP compliance.

| Environment | Verified here |
| --- | --- |
| CPython portable modules | Regression tests, UTF-8 CSV and official BCF 2.1 XSD validation |
| pyRevit IronPython 2.7 engine | All runtime Python sources compile |
| Revit API 25.5.10.0 | Required API members and category IDs inspected in the installed assembly |
| Revit 2024 / 2026 | Requires live validation; not exercised here |
| Revit 2025.5 native QA project, 2026-10-28 | 11/12 checks passed: host solid clashes, AABB rejection, clearance candidates, duplicates, Type Mark fallback, rooms, sheet collision/rollback, numbering and report exports |
| Linked solids | Not verified: both QA instances returned no document despite Loaded status; reload returned LinkOpenAsHost. Coverage correctly reports Incomplete |
| Native UI smoke checks | Create Sheets created one sheet; Add All collected 348 categorized records; Select All Rows selected those rows without selecting the model; Run Audit Quick Scan opened HTML and displayed Complete |
| Large RVT throughput / memory | Requires representative project measurements |

The 2025.5 checks ran in an isolated Hamdan-QA-Host project. The original user project
was not modified or saved. The QA construction template included an existing room;
room assertions account for that. Revit automatically joined the crossing wall fixture,
so two independent overlapping DirectShape solids provide the positive-volume test.
The native CSV/BCF exports succeeded; the live BCF's 19 XML documents also passed the
official BCF 2.1 schemas. Coordination-viewer interoperability remains untested.

The linked-model case is an unresolved fixture/session failure, not a passing geometry
test. A loaded link without an available API document is now distinguished from an
unloaded link and prevents a complete clash score. A fresh isolated session is required
to finish transformed/nested-link validation. The user has no 2.5 GB RVT available here,
so no large-model speed estimate is supported by this run.

## Live smoke cases

1. In a disposable project, place two crossing same-type beams with different endpoints:
   they must not be duplicate candidates. Coincident reversed curves should be candidates;
   different phases/design options/types should not be grouped.
2. Fill a door Type Mark on its type. Required-parameter completeness must read that value.
   Test a configured shared-parameter GUID and an explicitly type-scoped rule.
3. Workshared project: check Workset1 counts, a closed workset, and checkout ownership.
   Closed/unloaded content must leave the score incomplete, with a reason in Scan Notes.
4. Clash fixture: true solid penetration, boxes that overlap but solids do not, and nearby
   non-intersecting services. Only positive solid volume should become Hard. Clearance
   candidates must be labelled and excluded from score deductions.
5. Place the same RVT link twice, including a rotated/translated instance. Validate host-link
   and link-link results, nested transforms and BCF cameras. Unload a link and verify coverage.
6. Cancel collection/scan: no completed JSON baseline is saved, and the prior Element List
   remains intact. Add All Model Elements must not change Revit's current selection.
7. Create sheets with a loaded title block and with no title block. Check increments, number-first
   formatting, collision rejection and Cancel. Trigger a Revit assignment failure and confirm
   the entire batch rolls back. Test Undo after a successful batch.
8. Create a sheet containing only a placed schedule. Sheet Audit must count it as non-empty.
9. Audit model A, then model B; activate A and open its dashboard. It must open A's report.
   Resolve a warning occurrence while another of the same type remains: verify New/Resolved trends.
10. Import a multi-clash BCF into the coordination viewer used by the project. Confirm camera
    positioning and available component mapping, not just ZIP or schema validity.

Record timings and peak Revit memory on a small reference model before a large production model.
The native bounding-box prefilter and bounded solid cache reduce work, but cannot provide a
reliable duration estimate from RVT file size alone.
