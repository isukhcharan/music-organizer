import pytest

from musicorganizer.text import (
    album_identity_score,
    artist_match_score,
    artist_parts,
    clean_folder,
    norm,
    primary_artist,
    sim,
    tracknumber_value,
)


def test_norm_strips_extension_track_prefix_and_noise():
    assert norm("01 - Wah Kya Nazare.mp3") == "wah kya nazare"
    assert norm("295 (Official Video)") == "295"
    assert norm("Café_Song") == "cafe song"


def test_sim_blank_is_zero_and_identical_is_one():
    assert sim("", "x") == 0.0
    assert sim("Surma", "surma") == 1.0


def test_artist_credit_with_extra_collaborators_still_matches():
    assert artist_match_score("Sidhu Moose Wala / Byg Byrd", "Sidhu Moose Wala") >= 0.90
    assert artist_match_score("Singga", "Karan Aujla") < 0.62
    assert artist_match_score("", "Singga") == 0.0


def test_artist_parts_splits_common_separators():
    assert artist_parts("A & B feat. C, D / E") == ["A", "B", "C", "D", "E"]


def test_primary_artist_takes_first_credit():
    assert primary_artist("Jatinder Shah / Someone") == "Jatinder Shah"


def test_album_identity_ignores_generic_soundtrack_wording():
    assert album_identity_score("Ardaas", "Ardaas (Original Motion Picture Soundtrack)") >= 0.95
    assert album_identity_score("Ardaas", "Ardaas Karaan") < 0.82


@pytest.mark.parametrize("raw,expected", [("2/9", 2), (" 07", 7), ("", None), ("x", None)])
def test_tracknumber_value(raw, expected):
    assert tracknumber_value(raw) == expected


def test_clean_folder_removes_illegal_chars_and_uses_fallback():
    assert clean_folder('AC/DC: "Hits"?', "x") == "ACDC Hits"
    assert clean_folder("...", "Fallback") == "Fallback"
    assert len(clean_folder("a" * 300, "x")) == 100
