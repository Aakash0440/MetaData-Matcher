from mm import masking


def test_values_that_look_personal_are_masked_to_their_shape():
    rule = masking.detect("nino", ["AB123456C"] * 5)
    assert rule.personal and rule.value_based and rule.category == "direct_identifier"
    assert masking.safe_samples(["AB123456C", "jo@x.test"], rule) == ["XX999999X", "xx@x.xxxx"]


def test_name_hint_masks_even_when_values_look_harmless():
    rule = masking.detect("fst_nm", ["Amelia", "Oliver"])
    assert rule.personal and masking.safe_samples(["Amelia"], rule) == ["Xxxxxx"]


def test_free_text_is_redacted_and_truncated():
    out = masking.redact_text("Call 07700 900123 or jo@example.test, SW1A 1AA " + "x" * 200)
    assert "07700" not in out and "@" not in out and "SW1A" not in out and len(out) <= 80


def test_ordinary_columns_are_not_flagged():
    assert not masking.detect("hh_sz", ["1", "2", "3"]).personal
