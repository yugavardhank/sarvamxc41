from app import pipeline
from app.services import lists


def test_save_and_reorder_from_named_list(fresh_db):
    call = pipeline.start_call(7)  # Anita Sharma
    pipeline.handle_transcript(call["order_id"], "toor dal and salt", "en-IN")
    pipeline.handle_transcript(call["order_id"], "save this as my Sunday list", "en-IN")

    saved = lists.find_list_by_name(7, "sunday")
    assert saved is not None
    assert len(lists.get_list_items(saved["id"])) == 2

    call2 = pipeline.start_call(7)
    result = pipeline.handle_transcript(call2["order_id"], "order from my sunday list", "en-IN")
    assert result["total"] > 0
    assert len(result["items"]) == 2


def test_fuzzy_list_name_match(fresh_db):
    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "rice", "en-IN")
    pipeline.handle_transcript(call["order_id"], "save this as my weekly essentials", "en-IN")

    # "essentials" alone should still find "weekly essentials"
    found = lists.find_list_by_name(7, "essentials")
    assert found is not None
    assert found["name"] == "weekly essentials"


def test_unknown_list_name_is_reported_not_silently_ignored(fresh_db):
    call = pipeline.start_call(7)
    result = pipeline.handle_transcript(call["order_id"], "order from my diwali list", "en-IN")
    assert "couldn't find" in result["reply_text"]
