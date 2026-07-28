from foundry_lite.application.ports.content_index import is_classification_cleared
from foundry_lite.domain.classification import (
    CLASSIFICATION_RANKS,
    classification_rank,
    normalize_classification,
)


def test_classification_lattice_keeps_secret_and_restricted_as_highest_aliases() -> None:
    assert dict(CLASSIFICATION_RANKS) == {
        "UNCLASSIFIED": 0,
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "SECRET": 3,
        "RESTRICTED": 3,
    }
    assert classification_rank("secret") == classification_rank("RESTRICTED") == 3
    assert CLASSIFICATION_RANKS["CONFIDENTIAL"] < CLASSIFICATION_RANKS["SECRET"]


def test_classification_lattice_normalizes_empty_and_fails_closed_for_unknown_labels() -> None:
    assert normalize_classification(None) == "UNCLASSIFIED"
    assert normalize_classification(" confidential ") == "CONFIDENTIAL"
    assert classification_rank("future-unknown") is None


def test_content_clearance_uses_the_canonical_lattice_across_storage_casing() -> None:
    assert is_classification_cleared(" PUBLIC ", ("public",)) is True
    assert is_classification_cleared("INTERNAL", ("public", "internal")) is True
    assert is_classification_cleared("CONFIDENTIAL", ("public", "internal")) is False
    assert is_classification_cleared("future-unknown", ("public", "internal")) is False
