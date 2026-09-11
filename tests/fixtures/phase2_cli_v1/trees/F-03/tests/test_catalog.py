from src.catalog import Catalog


def test_catalog_count():
    catalog = Catalog()
    catalog.put("one", 1)
    assert catalog.count() == 1
