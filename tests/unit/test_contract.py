# The caller-data contract: closed field set, bounded values, inert renders.

import pytest

from src.services.contract import (
    CONTEXT_FIELDS,
    MAX_DOCUMENT_URL_CHARS,
    SUPPORTED_DOCUMENT_TYPES,
    ContractError,
    select_declared,
    validate_contract,
)

LIMIT = 20000

VALID = {
    "document_text": "製品名: 有機そば粉クッキー\n価格: 850円\n原材料: そば, 小麦, 卵",
    "document_url": "https://supplier.example/spec-4901234567894.pdf",
    "document_type": "PDF",
    "supplier_code": "supplier_00417",
}


class TestSelectDeclared:
    def test_undeclared_keys_are_dropped_not_ignored(self):
        """Dropping is what makes the closed contract true rather than aspirational.

        An ignored key stays in the structured parameters, reaches the first
        node's result, and is scanned by the framework's output gate there — so
        a value under a key this template never declared can fail the graph at
        node one however strict the declared contract is.
        """
        selected = select_declared({**VALID, "surprise": "x", "note": {"deep": 1}})
        assert set(selected) == set(VALID)
        assert "surprise" not in selected
        assert "note" not in selected

    def test_non_mapping_is_an_empty_contract(self):
        assert select_declared(None) == {}
        assert select_declared("string") == {}
        assert select_declared([1, 2]) == {}

    def test_declared_field_set_is_the_published_one(self):
        assert set(CONTEXT_FIELDS) == {
            "document_text",
            "document_url",
            "document_type",
            "supplier_code",
        }


class TestValidateContract:
    def test_valid_contract_round_trips(self):
        assert validate_contract(VALID, LIMIT) == VALID

    def test_document_type_is_normalised_and_closed(self):
        assert validate_contract({"document_type": "pdf"}, LIMIT)["document_type"] == "PDF"
        for supported in SUPPORTED_DOCUMENT_TYPES:
            assert validate_contract({"document_type": supported}, LIMIT)

    @pytest.mark.parametrize(
        "raw, field",
        [
            ({"document_type": "DOCX"}, "document_type"),
            ({"document_url": "https://ok/" + chr(32) + "bad"}, "document_url"),
            ({"document_url": "https://ok/<script>"}, "document_url"),
            ({"supplier_code": "SUPPLIER-0417"}, "supplier_code"),
            ({"supplier_code": "code with spaces"}, "supplier_code"),
            ({"supplier_code": "a" * 33}, "supplier_code"),
        ],
    )
    def test_out_of_alphabet_values_are_refused_by_field(self, raw, field):
        with pytest.raises(ContractError) as excinfo:
            validate_contract(raw, LIMIT)
        assert field in str(excinfo.value)

    @pytest.mark.parametrize(
        "value",
        [12345, 1.5, True, None, ["a"], {"a": 1}, b"bytes"],
    )
    def test_non_string_values_are_refused_fail_closed(self, value):
        """Every declared field is a string. A bool is not an int here either.

        Fail CLOSED: a value of the wrong type is refused rather than coerced,
        because coercion is how a caller-supplied structure becomes rendered
        output nobody validated.
        """
        with pytest.raises(ContractError):
            validate_contract({"document_text": value}, LIMIT)

    def test_document_text_is_bounded_by_the_configured_ceiling(self):
        assert validate_contract({"document_text": "a" * LIMIT}, LIMIT)
        with pytest.raises(ContractError) as excinfo:
            validate_contract({"document_text": "a" * (LIMIT + 1)}, LIMIT)
        assert "document_text" in str(excinfo.value)

    def test_a_misconfigured_ceiling_cannot_widen_the_contract(self):
        """The absolute ceiling holds whatever the configuration says."""
        with pytest.raises(ContractError):
            validate_contract({"document_text": "a" * 200_001}, 10**9)

    def test_document_url_length_is_bounded(self):
        with pytest.raises(ContractError):
            validate_contract({"document_url": "h" * (MAX_DOCUMENT_URL_CHARS + 1)}, LIMIT)

    @pytest.mark.parametrize(
        "raw",
        [
            {"document_text": "secret" * 4},
            {"supplier_code": "TOP-SECRET-CODE"},
            {"document_url": "https://x/" + chr(32) + "leak-me-please"},
        ],
    )
    def test_a_rejected_value_is_never_echoed(self, raw):
        """The message names the field. The value goes into logs; it must not."""
        try:
            validate_contract(raw, LIMIT)
        except ContractError as exc:
            for value in raw.values():
                assert str(value) not in str(exc)
