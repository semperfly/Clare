from pathlib import Path

import pytest

from clare.naming import parse_name, parse_stem


@pytest.mark.parametrize("stem, expected", [
    ("The.Dark.Knight.2008.1080p.BluRay.x265-SPARKS", "The Dark Knight (2008)"),
    ("inception.2010.dvdrip.xvid", "Inception (2010)"),
    ("movie_title_2015", "Movie Title (2015)"),
    ("2001.A.Space.Odyssey.1968.1080p", "2001 A Space Odyssey (1968)"),
    ("Blade.Runner.2049.2017.2160p.UHD", "Blade Runner 2049 (2017)"),
    ("Se7en.1995.1080p", "Se7en (1995)"),
    ("Spider-Man.2002.720p.BluRay", "Spider-Man (2002)"),
    ("WALL-E.2008.1080p", "WALL-E (2008)"),
    ("Aliens.1986.Special.Edition.1080p", "Aliens (1986) {edition-Special Edition}"),
    ("Blade.Runner.1982.Final.Cut.1080p", "Blade Runner (1982) {edition-Final Cut}"),
    ("the matrix 1999", "The Matrix (1999)"),
    ("Jaws [1975]", "Jaws (1975)"),
    ("Jaws.(1975).[720p]", "Jaws (1975)"),
    ("Heat 1080p BluRay", "Heat"),
])
def test_parse(stem, expected):
    assert parse_stem(stem).canonical_stem == expected


@pytest.mark.parametrize("stem", [
    "The Dark Knight (2008)",
    "Blade Runner 2049 (2017)",
    "Aliens (1986) {edition-Special Edition}",
    "Heat (1995) [1080p]",
])
def test_canonical_names_left_alone(stem):
    p = parse_stem(stem)
    assert p.is_canonical and not p.needs_rename


def test_folder_name_supplies_missing_year():
    p = parse_name(Path("/m/Heat (1995)/heat.dvdrip.xvid.avi"))
    assert p.canonical_stem == "Heat (1995)"
    assert p.source == "folder"


def test_folder_ignored_when_titles_differ():
    p = parse_name(Path("/m/Heat (1995)/Ronin.1998.avi"))
    assert p.canonical_stem == "Ronin (1998)"


def test_editions_are_not_duplicates():
    a = parse_stem("Aliens.1986.1080p")
    b = parse_stem("Aliens.1986.Special.Edition.1080p")
    assert a.key != b.key


def test_same_movie_same_key():
    a = parse_stem("The.Dark.Knight.2008.720p.xvid")
    b = parse_stem("The Dark Knight (2008)")
    assert a.key == b.key
