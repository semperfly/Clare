"""Parse movie filenames and build Plex/Jellyfin-style names.

Target format::

    Movie Title (Year).ext
    Movie Title (Year) {edition-Extended}.ext     # when an edition is detected
"""

import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

MAX_YEAR = datetime.date.today().year + 1

# Tokens that mark the end of the title in scene-style names. Used only when
# no year is present (otherwise everything after the year is discarded anyway).
_STOP_TOKENS = re.compile(
    r"\b(2160p|1080p|1080i|720p|576p|480p|4k|uhd|bluray|blu-ray|bdrip|brrip|"
    r"bdremux|remux|web-?dl|webrip|hdrip|hdtv|dvdrip|dvdscr|dvd5|dvd9|"
    r"x264|x265|h\.?264|h\.?265|hevc|xvid|divx|aac|ac3|dts|truehd|atmos)\b",
    re.IGNORECASE,
)

_EDITIONS = [
    (re.compile(r"\bextended(?:[ .]cut|[ .]edition)?\b", re.I), "Extended"),
    (re.compile(r"\bdirector'?s[ .]cut\b", re.I), "Director's Cut"),
    (re.compile(r"\btheatrical(?:[ .]cut)?\b", re.I), "Theatrical"),
    (re.compile(r"\bunrated\b", re.I), "Unrated"),
    (re.compile(r"\bultimate[ .]cut\b", re.I), "Ultimate Cut"),
    (re.compile(r"\bfinal[ .]cut\b", re.I), "Final Cut"),
    (re.compile(r"\bspecial[ .]edition\b", re.I), "Special Edition"),
    (re.compile(r"\bimax\b", re.I), "IMAX"),
]

_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_CANONICAL = re.compile(
    r"^(?P<title>[^\[\]{}_]+?) \((?P<year>\d{4})\)"
    r"(?: \{edition-(?P<edition>[^}]+)\})?"
    r"(?: - (?P<suffix>[^\[\]{}]+))?"          # "Title (Year) - Extended" (also accepted by Plex)
    r"(?: \[[^\]]*\])*$"
)
_ILLEGAL = re.compile(r'[<>:"/\\|?*]')
_MINOR = frozenset("a an the and but or for nor at by in of on to up as vs via".split())


@dataclass
class ParsedName:
    original: str                 # original filename stem
    title: Optional[str]
    year: Optional[int]
    edition: Optional[str]
    is_canonical: bool
    source: str = "filename"      # "filename" or "folder"

    @property
    def canonical_stem(self) -> Optional[str]:
        if not self.title:
            return None
        stem = self.title
        if self.year:
            stem += f" ({self.year})"
        if self.edition:
            stem += f" {{edition-{self.edition}}}"
        return stem

    @property
    def needs_rename(self) -> bool:
        return (not self.is_canonical and self.canonical_stem is not None
                and self.canonical_stem != self.original)

    @property
    def key(self) -> Optional[str]:
        """Identity used to detect duplicates: title + year + edition."""
        if not self.title:
            return None
        t = self.title.lower()
        t = re.sub(r"^(the|a|an) ", "", t)
        t = re.sub(r"[^\w ]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return f"{t}|{self.year or ''}|{(self.edition or '').lower()}"


def parse_name(path: Path) -> ParsedName:
    parsed = parse_stem(path.stem)
    folder = parse_stem(path.parent.name)

    # A well-named parent folder ("Movies/Heat (1995)/heat.dvdrip.avi") is
    # usually more trustworthy than the filename itself.
    if folder.is_canonical and folder.title:
        same_title = (parsed.title is None or
                      _loose(parsed.title) == _loose(folder.title))
        if same_title and (parsed.year is None or parsed.year == folder.year):
            if not parsed.is_canonical or parsed.year is None:
                return ParsedName(
                    original=parsed.original, title=folder.title, year=folder.year,
                    edition=parsed.edition or folder.edition,
                    is_canonical=False, source="folder",
                )
    return parsed


def parse_stem(stem: str) -> ParsedName:
    m = _CANONICAL.match(stem)
    if m and "." not in m.group("title").replace("...", "") and _valid_year(int(m.group("year"))):
        edition = m.group("edition")
        if not edition and m.group("suffix"):
            edition = _detect_edition(m.group("suffix")) or m.group("suffix").strip()
        return ParsedName(stem, m.group("title").strip(), int(m.group("year")),
                          edition, is_canonical=True)

    edition = _detect_edition(stem)

    work = re.sub(r"[\[{(]\s*((?:19|20)\d{2})\s*[\]})]", r" \1 ", stem)  # keep "[1975]" years
    work = re.sub(r"[\[{].*?[\]}]", " ", work)           # drop other [tags] / {ids}
    work = re.sub(r"(?<=\w)\.(?=\w)|_", " ", work)       # dots/underscores → spaces
    work = work.replace(".", " ")
    work = re.sub(r"\s+", " ", work).strip()

    year, pos = _find_year(work)
    if year is not None:
        raw_title = work[:pos]
    else:
        stop = _STOP_TOKENS.search(work)
        raw_title = work[:stop.start()] if stop else work
        # Strip a trailing "- GROUP" release tag when there was no year.
        raw_title = re.sub(r"-[A-Za-z0-9]+$", "", raw_title)

    # Remove edition words from the title itself.
    for pattern, _ in _EDITIONS:
        raw_title = pattern.sub(" ", raw_title)
    title = _clean_title(raw_title)
    return ParsedName(stem, title, year, edition, is_canonical=False)


# ---------------------------------------------------------------------------

def _valid_year(y: int) -> bool:
    return 1900 <= y <= MAX_YEAR


def _find_year(text: str) -> Tuple[Optional[int], int]:
    """Pick the release year: the last plausible year that has a title before it.

    Handles "2001 A Space Odyssey 1968" (→1968) and "Blade Runner 2049 2017" (→2017).
    """
    cands = [(m.start(), int(m.group(1))) for m in _YEAR.finditer(text)
             if _valid_year(int(m.group(1)))]
    for pos, y in reversed(cands):
        before = text[:pos].strip(" -([")
        if before:
            return y, pos
    return None, -1


def _detect_edition(text: str) -> Optional[str]:
    for pattern, name in _EDITIONS:
        if pattern.search(text):
            return name
    return None


def _clean_title(raw: str) -> Optional[str]:
    s = _ILLEGAL.sub(" ", raw)
    s = re.sub(r"[()\[\]]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -,;")
    if not s:
        return None
    return _title_case(s)


def _title_case(s: str) -> str:
    """Capitalise lowercase words; leave words that already have capitals alone
    (keeps "Se7en", "WALL-E", "McQueen", "II")."""
    words = s.split(" ")
    out = []
    for i, w in enumerate(words):
        if w != w.lower():
            out.append(w)
        elif 0 < i < len(words) - 1 and w in _MINOR:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _loose(title: str) -> str:
    t = re.sub(r"^(the|a|an) ", "", title.lower())
    return re.sub(r"[^a-z0-9]", "", t)
