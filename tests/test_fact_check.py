import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fact_check import collect, conflicts  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "site"
HERE = Path(__file__).resolve().parent


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


def test_ai_json_prices_in_any_shape_are_forbidden():
    # Regression (external acceptance F1): price key, nested offers.price
    # and a currency amount in free text must all be caught.
    _, forbidden = collect(HERE / "fixtures" / "ai-prices")
    assert len(forbidden) >= 3, forbidden


def test_other_entities_names_and_urls_are_not_business_facts():
    # Regression (external acceptance F2/F3): FAQ questions, article
    # names and per-page URLs must not merge into the business's facts.
    facts, _ = collect(HERE / "fixtures" / "entities")
    assert set(facts["name"]) == {"Example Studio"}
    assert "name" not in conflicts(facts)
    assert "url" not in conflicts(facts)


def test_same_entity_name_drift_is_still_caught():
    facts, _ = collect(HERE / "fixtures" / "entities-drift")
    assert "name" in conflicts(facts)


def test_yuan_suffix_and_wellknown_ai_txt_are_forbidden():
    _, forbidden = collect(FIXTURE)
    descs = [d for d, _ in forbidden]
    assert any("1300" in d for d in descs)  # 1300元 in llms.txt
    assert any("999" in d for d in descs)   # 999元 in .well-known/ai.txt


def test_invalid_jsonld_is_reported_not_skipped():
    _, forbidden = collect(FIXTURE)
    assert any("invalid JSON-LD" in d for d, _ in forbidden)


def test_bare_number_price_list_in_ai_txt_is_forbidden():
    _, forbidden = collect(FIXTURE)
    descs = [d for d, s in forbidden if "ai.txt" in s]
    assert any("1300" in d for d in descs)  # bare number under 价格：
    assert any("2300" in d for d in descs)
    assert any("3800" in d for d in descs)


def test_negated_price_is_not_a_price():
    _, forbidden = collect(FIXTURE)
    assert not any("2500" in d for d, _ in forbidden)  # （无 2500 档）


def test_negation_covers_marked_amounts_and_full_wordlist():
    _, forbidden = collect(HERE / "fixtures" / "negation")
    # 无 ¥2500 (marked), no 2500元 (marked), 不设 2500 (bare): all exempt
    assert not any("2500" in d for d, _ in forbidden)


def test_nearby_unrelated_negation_does_not_suppress_a_real_price():
    # A negation that governs something else in the same sentence —
    # 'no fee', '无预约费' — must not exempt the price after it; only a
    # negation immediately adjacent to the amount does. The fixture
    # holds three probe lines (bare, marked, 元-suffixed); findings are
    # deduplicated per (amount, file), so they surface as one ¥1300
    # finding — the pre-fix behaviour returned none at all.
    _, forbidden = collect(HERE / "fixtures" / "negation-nearby")
    assert len(forbidden) == 1, forbidden
    assert "1300" in forbidden[0][0]


def test_distant_negation_does_not_suppress_a_real_price():
    _, forbidden = collect(HERE / "fixtures" / "negation")
    # "no appointment needed, signature service 1300": the "no" is not
    # adjacent to 1300, so the real price is still reported.
    assert any("1300" in d for d, _ in forbidden)
    assert len(forbidden) == 1


def test_address_spellings_of_same_address_do_not_conflict():
    facts, _ = collect(HERE / "fixtures" / "address-variants")
    assert "address" not in conflicts(facts)


def test_real_address_drift_is_still_caught():
    facts, _ = collect(HERE / "fixtures" / "address-drift")
    assert "address" in conflicts(facts)


def test_nested_and_freetext_phones_are_pooled():
    facts, _ = collect(HERE / "fixtures" / "ai-phones")
    assert len(facts["phone"]) == 1  # booking.telephone + FAQ answer


def test_nested_phone_drift_is_caught():
    facts, _ = collect(HERE / "fixtures" / "ai-phones-drift")
    assert "phone" in conflicts(facts)


if __name__ == "__main__":
    test_finds_phone_conflict_between_jsonld_and_text()
    test_finds_price_conflict()
    test_consistent_name_is_not_a_conflict()
    test_price_in_llms_txt_is_forbidden_not_a_fact()
    test_ai_json_prices_in_any_shape_are_forbidden()
    test_other_entities_names_and_urls_are_not_business_facts()
    test_same_entity_name_drift_is_still_caught()
    test_yuan_suffix_and_wellknown_ai_txt_are_forbidden()
    test_invalid_jsonld_is_reported_not_skipped()
    test_bare_number_price_list_in_ai_txt_is_forbidden()
    test_negated_price_is_not_a_price()
    test_negation_covers_marked_amounts_and_full_wordlist()
    test_nearby_unrelated_negation_does_not_suppress_a_real_price()
    test_distant_negation_does_not_suppress_a_real_price()
    test_address_spellings_of_same_address_do_not_conflict()
    test_real_address_drift_is_still_caught()
    test_nested_and_freetext_phones_are_pooled()
    test_nested_phone_drift_is_caught()
    print("all tests passed")
