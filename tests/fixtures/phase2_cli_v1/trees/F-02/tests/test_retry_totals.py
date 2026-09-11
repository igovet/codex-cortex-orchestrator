from src.retry_totals import total


def test_fixture_is_importable():
    assert total([("a", 5)]) == 5
