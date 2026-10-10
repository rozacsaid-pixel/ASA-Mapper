# ASA Mapper — Design

Date: 2026-10-10
Status: Draft for review

## 1. Purpose

Digitization Specialists (DS) map Anduin form fields to Anduin Standard Aliases (ASAs) by hand. ASA Mapper reads a form export, proposes ASAs for each field and option from the ASA Library, lets a DS review the proposals, and produces a review sheet.

Success criteria:
- On the known-mapped test set, first-choice accuracy beats the plain-text baseline (fields 63%, options 66%), and accuracy is reported on every scoring or library change.
- A DS can review a typical subscription booklet (~150–200 fields) in one sitting and download the result.
- Changes to the ASA Library reach the app without code changes.

## 2. Inputs

### 2.1 Form export (.zip or folder)
Produced by the Anduin form builder. Only `form_data.json` is required:
- `form.namespaceFormSchemaMap.main.schema`: nested tree of field names and titles (pages → groups → fields).
- `form.namespaceFormSchemaMap.main.uiSchema[<key>]`: per-field widget (`ui:widget`), label (`ui:formattedText`, HTML), options (`ui:multipleOption.options` = list of `[optionKey, {formattedText, pdfMappings, blueprintMetadataMapping}]`), PDF links (`ui:pdfMapping`), visibility (`ui:invisible`), custom format (`ui:customFormat`).

Existing ASAs in an export are read from:
- Field level: entries in `ui:pdfMapping` that start with `asa_`.
- Option level: entries in an option's `pdfMappings` that start with `asa_`.
- Field key: a field whose key starts with `asa_`.

Known limitation: the form builder's own ASA count (e.g. 187 for 8VC Fund VI version 36) includes mappings that are not in the export. Only the ~146 visible ones are available. The rest can be added later from another source (e.g. the Anduin MCP connector once authentication works).

### 2.2 ASA Library
Google Sheet "ASA Library" (`1vXaCq5YjwfnUw_OsPilJ5WrXe5JHaFPZoKD4XejfhXk`), **"ASA List" tab only**. Header row is row 2. Columns used:
`Section`, `Field group`, `Field meaning summary`, `Field meaning full definition`, `Keywords`, `Field name`, `Field option`, `Type` (`Field name` | `Field option`), `Available under field name`, `Data type`, `Input type in the form`.

Sources (both supported):
- Google Sheets API through a service account the sheet is shared with; refreshed on demand ("Refresh" button) with the last sync time shown.
- Manual upload of the sheet as `.xlsx` (fallback).

If the sync fails, the last good copy stays in use and the UI shows its date.

## 3. Matching rules

### 3.1 What is matched
| Form element | Matched to |
|---|---|
| TextBox and text-like widgets (Email, Phone, Date, Money, Percentage, Integer, TextArea, Country, State, CustomFormat, Dropdown, MultipleSuggest) | Field name ASA |
| Radio group | Field name ASA |
| MultipleCheckbox group | Field name ASA |
| Radio option / MultipleCheckbox option | Field option ASA, restricted to options whose `Available under field name` equals the field's accepted (or top-suggested) Field name ASA |

Skipped and listed with a reason: layout widgets (Group, Paragraph, Header, Page, Pdf), Signature, File, FileGroup, and helper fields (keys matching `is_*`, `split*`, `field_below`, `radio_uncheck`, `shortcut`, and hidden fields titled "For Mapping" / "combine all address fields"). A DS can un-skip any field.

### 3.2 Field description
For each field: cleaned label (HTML stripped, footnote spans removed), full section/group path from the schema tree, nearest preceding heading/paragraph, and option labels. For each option: option label plus the parent field's label.

### 3.3 Scoring (version 1, no AI)
Score = weighted sum of:
1. **Text similarity**: TF-IDF (word 1–2-grams) cosine between the field description and the ASA's `Section + Field group + Field meaning summary + Field meaning full definition + Keywords`.
2. **Type compatibility**: an ASA whose `Input type in the form` conflicts with the widget (e.g. Textbox ASA for a Radio) is excluded; a match gets a small boost.
3. **Past mappings**: if a stored mapping has a near-identical label (normalised text similarity ≥ 0.9) and a similar section path, its ASA gets a strong boost.

Weights are tuned with the evaluation in §6. Output: top 3 per field and per option, each with confidence High / Medium / Low from score thresholds set during tuning.

When a field's Field name ASA is changed, its options are re-ranked against the new parent.

### 3.4 Checks on existing mappings
- Existing ASA not in the library → flag "not in library" (e.g. `asa_commitment_amount`).
- Existing ASA differs from a High-confidence suggestion → flag "possible mismatch" (e.g. Country(ies) of Citizenship → `asa_countryofbirth_generalinfo`).
- After a library refresh, accepted ASAs that no longer exist are flagged.

### 3.5 Later: Claude ranking step
Not in version 1. Planned as a separate step that takes the top ~15 candidates and the field context and returns the top 3 with reasons. It is added only if the evaluation shows a meaningful gain.

## 4. Screens

1. **Upload**: drop a `.zip`. Shows form name, version (from filename), field count, count of existing ASAs, and library status (rows, last sync, Refresh, Upload .xlsx).
2. **Review**: table grouped by page → section in form order. Field rows: label, key, type, existing ASA, top suggestion, confidence, Accept / Change / No ASA. Radio/checkbox rows expand to option sub-rows. "Change" opens a library search with the alternatives first. Filters: Needs review (Low confidence, conflict, not in library), Accepted, Skipped, by section. "Accept all High" per section. Progress indicator. Autosave per form + version.
3. **Accuracy** (admin): runs the evaluation and shows first-choice and top-3 accuracy plus the list of misses.
4. **Downloads**: review sheet `.xlsx`. The re-import file stays hidden until §8 is resolved.

## 5. Outputs

### 5.1 Review sheet (.xlsx)
One row per field and per option: page, section, label, field key, option key, widget, existing ASA, suggested ASA, alternatives (2), confidence, accepted ASA, status (accepted / changed / no ASA / skipped), flags, reviewer, review time.

### 5.2 Re-import file (deferred)
Updated `form_data.json`, re-zipped. Blocked until we confirm (a) that the form builder accepts a modified export and (b) where ASAs should be written (PDF links, field keys, `blueprintMetadataMapping`, or elsewhere).

### 5.3 Past mappings
On "Finish review", accepted pairs (label, section path, widget, ASA) are saved to the past-mappings store. Seeded from the ~144 known mappings in 8VC Fund VI version 36.

## 6. Evaluation

- Test set: fields and options with existing library ASAs in the mapped exports (start: 8VC Fund VI v36, 49 field-level and 93 option-level mappings in the library).
- Metrics: field first-choice and top-3 accuracy; option first-choice accuracy with the correct parent given; option accuracy end to end.
- To avoid leakage, past mappings from the form being evaluated are excluded during its evaluation (leave-one-form-out).
- Known errors in the test set (e.g. citizenship → country of birth) are kept in an "exceptions" file and reported separately.

## 7. Architecture

Single Docker container: FastAPI backend + React frontend (built static files served by FastAPI) + SQLite file on a mounted volume.

| Component | Responsibility | Depends on |
|---|---|---|
| `export_parser` | zip/folder → list of `FormField` (key, widget, label, path, options, existing ASAs, skip reason) | — |
| `asa_library` | Google Sheet / xlsx → list of `AsaEntry`; validation (missing columns, duplicate ASAs, options whose parent is missing) | Google service account (optional) |
| `matcher` | `FormField` + library + past mappings → ranked suggestions | — (pure) |
| `past_mappings` | store and look up accepted mappings | SQLite |
| `evaluate` | accuracy report on mapped exports | parser, matcher |
| `exporter` | review sheet xlsx | openpyxl |
| `api` | upload, suggestions, save decisions, download, library refresh, accuracy | all above |
| `web` | upload / review / accuracy UI | api |

Storage (SQLite): library snapshot + sync time; review sessions (form name, version, field decisions); past mappings. Uploaded exports are deleted after parsing; only the extracted field text is kept.

Configuration (environment variables): Google OAuth client and allowed domain (`anduintransact.com`), Google service account JSON, sheet ID and tab name, data directory.

Hosting: undecided. Version 1 runs with `docker compose up`; the same image can deploy to Dokploy, Vercel or another container host.

## 8. Open questions
1. Where the form builder stores the ASA mappings missing from the export, and whether a modified export can be re-imported. This blocks §5.2.
2. Hosting target and who administers the service account.
3. Confidence thresholds and the accuracy targets that would justify adding the Claude step. These are set after the first evaluation run.

## 9. Error handling
- Upload without `form_data.json` or with an unexpected structure: clear error, nothing saved.
- Google sync failure: keep the last good library and show the warning with its date.
- Library rows with missing required columns: reported and skipped.
- Library change after review: keep decisions and flag ASAs that no longer exist.

## 10. Testing
- Unit tests for parser, library loader, and matcher, using the three shared exports as fixtures (8VC Entrepreneurs Fund VI v5, 8VC Fund VI v7 and v36) and a small library sample. Client exports contain fund documents, so they live in a git-ignored `fixtures/private/` folder (path set by an environment variable). Committed tests use a small synthetic export.
- Accuracy test: fails if first-choice accuracy drops below the current baseline (start: fields 63%, options 66%; raised as scoring improves).
- End-to-end: upload zip → accept → download sheet → check rows.

## 11. Out of scope (version 1)
Claude ranking, the other library tabs (WIP, tax forms), comments and collaboration, user roles beyond Google sign-in, re-import file.
