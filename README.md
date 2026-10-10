# ASA Mapper

Suggests Anduin Standard Aliases (ASAs) for the fields and options of an Anduin form export, lets a DS review them, and downloads a review sheet.

Design: `docs/superpowers/specs/2026-10-10-asa-mapper-design.md`

## Run locally

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/uvicorn asa_mapper.api:create_app --factory --port 8000

Open http://localhost:8000. Load the ASA Library with **Upload library .xlsx** (in Google Sheets: File → Download → Microsoft Excel), then drop a form export `.zip`.

## Command line

    .venv/bin/python -m asa_mapper.cli library path/to/ASA\ Library.xlsx   # import the library
    .venv/bin/python -m asa_mapper.cli seed path/to/mapped-export ...       # store known mappings
    .venv/bin/python -m asa_mapper.cli evaluate                             # accuracy on known mappings

## How suggestions work (version 1, no AI)

- Text fields, Radio groups and Checkbox groups are matched to **Field name** ASAs; Radio/Checkbox options to **Field option** ASAs under the field's ASA.
- Score = TF-IDF text similarity (label, nearby headings, section path, option labels vs. the ASA's section, field group, meaning, keywords), halved when the ASA's input type doesn't fit the widget, plus a boost when the same label was mapped before.
- Finished reviews are saved as past mappings and improve later suggestions.

## Configuration

See `.env.example`. Google Sheet sync needs a service account the sheet is shared with. Google sign-in is off unless `ASA_GOOGLE_CLIENT_ID` is set.

## Data

Client exports and the library copy live in `fixtures/private/` and `data/`, both git-ignored. Uploaded exports are parsed in memory and not stored.

## Tests

    .venv/bin/pytest

## Current accuracy

Measured against the form builder's own ASA export for 8VC Fund VI, L.P. v36 (185 mappings in the ASA List), with no past mappings: a brand-new form. Options are scored end to end, i.e. using the app's own field suggestion as the parent hint.

| | Tested | First suggestion correct | In top 3 |
|---|---|---|---|
| Field names | 84 | 66% | 80% |
| Field options | 101 | 82% | 94% |

With v36 stored as past mappings, a similar form (8VC Fund VI v7) gets 57 of 62 field ASAs and 94 of 97 option ASAs right as first suggestion.

Most remaining misses are address and contact fields whose meaning is only in the section (residence vs place of business vs primary contact), plus a few likely errors in the v36 data itself (Yes/No reversed, citizenship mapped to country of birth).

## System ASA export

The form builder's ASA export is plain text, one mapping per line: `field_key-->asa` or `field_key#option_key-->asa`.

- Upload it with a form export to use it as the existing ASAs (instead of PDF links).
- `python -m asa_mapper.cli seed <export> --asa <file.txt>` stores it as past mappings and test data.
- **Download ASA list (.txt)** on a review writes the accepted ASAs in the same format.
