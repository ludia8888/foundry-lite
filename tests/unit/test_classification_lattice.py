from foundry_lite.domain.classification import (
    CLASSIFICATION_RANKS,
    classification_rank,
    normalize_classification,
)


def test_classification_lattice_keeps_secret_and_restricted_as_highest_aliases() -> None:
    assert classification_rank("secret") == classification_rank("RESTRICTED") == 3
    assert CLASSIFICATION_RANKS["CONFIDENTIAL"] < CLASSIFICATION_RANKS["SECRET"]


def test_classification_lattice_normalizes_empty_and_fails_closed_for_unknown_labels() -> None:
    assert normalize_classification(None) == "UNCLASSIFIED"
    assert normalize_classification(" confidential ") == "CONFIDENTIAL"
    assert classification_rank("future-unknown") is None
