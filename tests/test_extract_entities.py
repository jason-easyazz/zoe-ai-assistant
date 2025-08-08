import asyncio
import importlib.util
import datetime

spec = importlib.util.spec_from_file_location("zoe_core_main", "services/zoe-core/main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def test_extract_birthday_event_title():
    text = "My birthday is on May 5."
    entities = asyncio.run(main.extract_entities_advanced(text))
    assert entities["events"], "No events detected"
    event = entities["events"][0]
    assert event["title"] == "May 5"
    assert isinstance(event["date"], datetime.date)
