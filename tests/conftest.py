import pytest

from tests.helpers import make_library_xlsx, write_export, zip_export


@pytest.fixture
def export_dir(tmp_path):
    return write_export(tmp_path / "Sample Export - Version 3")


@pytest.fixture
def export_zip(export_dir):
    return zip_export(export_dir)


@pytest.fixture
def library_xlsx():
    return make_library_xlsx()


@pytest.fixture
def library(library_xlsx):
    from asa_mapper.asa_library import load_library_xlsx
    return load_library_xlsx(library_xlsx)


@pytest.fixture
def template_xlsx():
    from tests.helpers import make_template_xlsx
    return make_template_xlsx()
