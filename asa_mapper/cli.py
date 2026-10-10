"""Command line: import the library, seed past mappings from mapped exports, print accuracy."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .asa_library import load_library_xlsx
from .evaluate import evaluate
from .export_parser import form_label, parse_export
from .past_mappings import mappings_from_form
from .settings import Settings
from .store import Store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m asa_mapper.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    lib = sub.add_parser("library", help="Import the ASA Library from an .xlsx file")
    lib.add_argument("xlsx", type=Path)
    seed = sub.add_parser("seed", help="Store existing ASA mappings from mapped form exports")
    seed.add_argument("exports", nargs="+", type=Path)
    sub.add_parser("evaluate", help="Print accuracy on stored mappings")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    store = Store(settings.data_dir / "asa_mapper.sqlite")
    if args.cmd == "library":
        data = args.xlsx.read_bytes()
        library = load_library_xlsx(data, settings.sheet_tab)
        store.save_library(data, f"Uploaded {args.xlsx.name}")
        names = len(library.names())
        print(f"Loaded {names} field names, {len(library.entries) - names} options, {len(library.issues)} issues")
        return 0

    saved = store.load_library()
    if not saved:
        print("Load the library first: python -m asa_mapper.cli library <file.xlsx>", file=sys.stderr)
        return 1
    library = load_library_xlsx(saved[0], settings.sheet_tab)

    if args.cmd == "seed":
        for path in args.exports:
            form = parse_export(path)
            form.name = form_label(form.name, path.name)
            mappings = mappings_from_form(form, library)
            store.replace_past(form.name, mappings)
            print(f"Seeded {len(mappings)} mappings from {form.name}")
        return 0

    report = evaluate(library, store.list_past())
    pct = lambda x: "-" if x is None else f"{x:.0%}"  # noqa: E731
    print(f"Forms: {report['forms']}")
    for label, key in (("Field names", "fields"), ("Field options", "options")):
        b = report[key]
        print(f"{label}: {b['total']} tested, first choice {pct(b['top1_rate'])}, "
              f"top {report['top_n']} {pct(b['topn_rate'])}")
    for miss in report["misses"][:20]:
        print(f"  MISS [{miss['kind']}] {miss['label'][:60]!r}: expected {miss['expected']}, got {miss['got'] or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
