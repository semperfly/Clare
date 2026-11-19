"""Scan a library and work out what should be fixed.

Both the report and the fixer use the same ``Plan`` so that what the report
says is exactly what ``fix --execute`` does.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional

from .analyzer import TIER_RANK, VIDEO_EXTENSIONS, MediaInfo, probe_file
from .naming import ParsedName, parse_name

# Copies whose running times differ by more than this are probably different
# cuts, so they are never auto-deleted as duplicates.
DURATION_TOLERANCE = 0.05
# Ignore tiny files such as trailers and samples.
DEFAULT_MIN_SIZE_MB = 50


@dataclass
class Item:
    path: Path
    info: MediaInfo
    name: ParsedName


@dataclass
class DuplicateGroup:
    title: str
    items: List[Item]
    keep: Item
    remove: List[Item] = field(default_factory=list)
    review: List[Item] = field(default_factory=list)   # dupes we won't auto-delete
    review_reason: Optional[str] = None


@dataclass
class Rename:
    item: Item
    target: Path
    companions: List[tuple]          # (old Path, new Path)


@dataclass
class SkippedRename:
    item: Item
    reason: str


@dataclass
class Plan:
    root: Path
    items: List[Item]
    min_height: int
    duplicates: List[DuplicateGroup]
    renames: List[Rename]
    skipped_renames: List[SkippedRename]

    @property
    def substandard(self) -> List[Item]:
        return [i for i in self.items if not i.info.is_acceptable(self.min_height)]

    @property
    def errors(self) -> List[Item]:
        return [i for i in self.items if i.info.error]

    @property
    def deletions(self) -> List[Item]:
        return [it for g in self.duplicates for it in g.remove]

    @property
    def needs_new_backup(self) -> List[Item]:
        """One entry per movie whose *best* copy is still below standard."""
        removed = {id(i) for i in self.deletions}
        best: Dict[str, Item] = {}
        for it in self.items:
            if id(it) in removed or it.info.error:
                continue
            k = it.name.key or str(it.path)
            if k not in best or rank(it) > rank(best[k]):
                best[k] = it
        return sorted((i for i in best.values() if not i.info.is_acceptable(self.min_height)),
                      key=lambda i: str(i.path).lower())


def rank(item: Item):
    info = item.info
    v = info.video
    return (
        0 if info.error else 1,
        TIER_RANK.get(info.tier, 0),
        info.effective_height,
        1 if v and v.hdr else 0,
        info.bitrate_mbps or 0,
        info.size,
    )


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def find_videos(root: Path, recursive: bool = True, min_size_mb: float = DEFAULT_MIN_SIZE_MB) -> List[Path]:
    paths: Iterator[Path] = root.rglob("*") if recursive else root.iterdir()
    min_bytes = min_size_mb * 1024 * 1024
    out = []
    for p in paths:
        if p.suffix.lower() not in VIDEO_EXTENSIONS or p.name.startswith("._"):
            continue  # "._x.mkv" are macOS metadata files, common on NAS shares
        if any(part.startswith(".") or part.startswith("@") for part in p.relative_to(root).parts[:-1]):
            continue  # hidden dirs, Synology @eaDir, etc.
        try:
            if p.is_file() and p.stat().st_size >= min_bytes:
                out.append(p)
        except OSError:
            continue
    return sorted(out, key=lambda p: str(p).lower())


def analyze(paths: List[Path], workers: int = 4,
            progress: Optional[Callable[[], None]] = None) -> List[Item]:
    def one(p: Path) -> Item:
        item = Item(p, probe_file(p), parse_name(p))
        if progress:
            progress()
        return item

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, paths))


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def build_plan(root: Path, items: List[Item], min_height: int = 1080) -> Plan:
    duplicates = _find_duplicates(items)
    deleting = {id(i) for g in duplicates for i in g.remove}
    renames, skipped = _plan_renames([i for i in items if id(i) not in deleting], deleting, items)
    return Plan(root, items, min_height, duplicates, renames, skipped)


def _find_duplicates(items: List[Item]) -> List[DuplicateGroup]:
    groups: Dict[str, List[Item]] = {}
    for it in items:
        if it.name.key:
            groups.setdefault(it.name.key, []).append(it)

    result = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=rank, reverse=True)
        keep = ordered[0]
        g = DuplicateGroup(title=keep.name.canonical_stem or keep.path.stem,
                           items=ordered, keep=keep)
        for other in ordered[1:]:
            reason = _unsafe_to_delete(keep, other)
            if reason:
                g.review.append(other)
                g.review_reason = reason
            else:
                g.remove.append(other)
        result.append(g)
    return sorted(result, key=lambda g: g.title.lower())


def _unsafe_to_delete(keep: Item, other: Item) -> Optional[str]:
    if keep.info.error:
        return "best copy could not be analyzed"
    if other.info.error:
        return "a copy could not be analyzed"
    a, b = keep.info.duration, other.info.duration
    if not a or not b:
        return "running time unknown"
    if abs(a - b) / max(a, b) > DURATION_TOLERANCE:
        return f"running times differ ({a / 60:.0f} vs {b / 60:.0f} min) — may be a different cut"
    return None


def _companions(video: Path, new_stem: str) -> List[tuple]:
    """Subtitles/NFO/artwork named after the video, e.g. 'Movie.en.srt'."""
    out = []
    prefix = video.stem + "."
    try:
        siblings = list(video.parent.iterdir())
    except OSError:
        return out
    for s in siblings:
        if s == video or s.suffix.lower() in VIDEO_EXTENSIONS:
            continue
        if s.name.startswith(prefix):
            out.append((s, video.parent / (new_stem + s.name[len(video.stem):])))
    return out


def _plan_renames(candidates: List[Item], deleting: set, all_items: List[Item]):
    renames: List[Rename] = []
    skipped: List[SkippedRename] = []
    by_path = {i.path: i for i in all_items}
    claimed = set()

    for it in candidates:
        if it.name.is_canonical:
            continue
        stem = it.name.canonical_stem
        if not stem:
            skipped.append(SkippedRename(it, "could not work out the title"))
            continue
        if it.name.year is None:
            skipped.append(SkippedRename(it, f"no year found — suggest '{stem} (YEAR){it.path.suffix}'"))
            continue
        target = it.path.with_name(stem + it.path.suffix)
        if target == it.path:
            continue
        occupant = by_path.get(target)
        same_file = target.exists() and target.samefile(it.path) if target.exists() else False
        if target in claimed:
            skipped.append(SkippedRename(it, f"another copy is being renamed to '{target.name}'"))
            continue
        if target.exists() and not same_file and not (occupant and id(occupant) in deleting):
            skipped.append(SkippedRename(it, f"'{target.name}' already exists in that folder"))
            continue
        claimed.add(target)
        renames.append(Rename(it, target, _companions(it.path, stem)))
    return renames, skipped
