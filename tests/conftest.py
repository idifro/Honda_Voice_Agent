import pytest

from honda_voice.db.catalog import Catalog
from honda_voice.db.catalog_seed import build_rows


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    """Offline catalog from the validated JSON: the same Catalog class, no database (no trigram fallback)."""
    return Catalog.from_seed_rows(build_rows())
