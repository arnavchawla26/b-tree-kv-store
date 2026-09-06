import pytest


@pytest.fixture()
def tmp_db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def small_page_size():
    """A page size tiny enough that a few dozen keys force multiple splits."""
    return 256
