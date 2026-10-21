import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fact_check import collect, conflicts  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "site"


def test_finds_phone_conflict_between_jsonld_and_text():
    facts, _ = collect(FIXTURE)
    assert set(facts["phone"]) == {"13800001111", "13900002222"}
    assert "phone" in conflicts(facts)


def test_finds_price_conflict():
    facts, _ = collect(FIXTURE)
    assert set(facts["price"]) == {"1300", "1500"}
    assert "price" in conflicts(facts)


def test_consistent_name_is_not_a_conflict():
    facts, _ = collect(FIXTURE)
    assert "name" not in conflicts(facts)


def test_price_in_llms_txt_is_forbidden_not_a_fact():
    facts, forbidden = collect(FIXTURE)
    assert any("AI-facing" in d for d, _ in forbidden)
    # the llms.txt amount must not join the page-price consistency pool
    assert set(facts["price"]) == {"1300", "1500"}


if __name__ == "__main__":
    test_finds_phone_conflict_between_jsonld_and_text()
    test_finds_price_conflict()
    test_consistent_name_is_not_a_conflict()
    test_price_in_llms_txt_is_forbidden_not_a_fact()
    print("all tests passed")
