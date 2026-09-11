from src.event_log import format_event


def test_fixture_is_importable():
    assert "event=login" in format_event("login", "fixture-token")
