from asa_mapper.cli import main


def test_library_seed_evaluate(tmp_path, monkeypatch, capsys, library_xlsx, export_dir):
    monkeypatch.setenv("ASA_DATA_DIR", str(tmp_path / "data"))
    xlsx = tmp_path / "lib.xlsx"
    xlsx.write_bytes(library_xlsx)
    assert main(["evaluate"]) == 1
    assert main(["library", str(xlsx)]) == 0
    assert main(["seed", str(export_dir)]) == 0
    assert "Seeded 4 mappings from Sample Fund - Subscription Booklet (Version 3)" in capsys.readouterr().out
    assert main(["evaluate"]) == 0
    assert "Field names" in capsys.readouterr().out
