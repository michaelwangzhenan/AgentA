"""ingest_cancel 注册表 UT。"""

from src.services import ingest_cancel


def test_register_trigger_unregister() -> None:
    ev = ingest_cancel.register("a")
    assert not ev.is_set()
    assert ingest_cancel.trigger("a") is True
    assert ev.is_set()
    ingest_cancel.unregister("a")
    assert ingest_cancel.trigger("a") is False
