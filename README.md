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

Measured on 8VC Fund VI, L.P. v36 (151 known mappings), each form scored without its own mappings:

| | Tested | First suggestion correct | In top 3 |
|---|---|---|---|
| Field names | 58 | 66% | 78% |
| Field options | 93 | 80% | 97% |

Most remaining field misses need context the text alone doesn't give: wire instructions *from* vs *to*, individual vs joint investor name, and generic labels like "Phone Number". Past mappings from finished reviews fix repeated wording; a Claude ranking step is the planned next improvement.
