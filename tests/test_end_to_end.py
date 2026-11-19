"""Builds a small library of real video files with FFmpeg and runs Clare on it."""

import shutil
import subprocess
from pathlib import Path

import pytest

from clare import analyzer
from clare.cli import main
from clare.fixer import apply_plan
from clare.library import analyze, build_plan, find_videos

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_video(path: Path, size: str, codec: str, seconds: int = 2):
    path.parent.mkdir(parents=True, exist_ok=True)
    enc = {"h264": ["-c:v", "libx264", "-preset", "ultrafast"],
           "hevc": ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=none"],
           "xvid": ["-c:v", "mpeg4", "-vtag", "XVID"]}[codec]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=size={size}:rate=24:duration={seconds}",
                    *enc, str(path)], check=True)


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    root = tmp_path_factory.mktemp("Movies")
    tdk = root / "The Dark Knight (2008)"
    make_video(tdk / "The.Dark.Knight.2008.1080p.BluRay.x264.mkv", "1920x1080", "h264")
    make_video(tdk / "dark knight 2008 dvdrip.avi", "720x480", "xvid")
    make_video(root / "Heat (1995)" / "heat.dvdrip.xvid.avi", "640x352", "xvid")
    (root / "Heat (1995)" / "heat.dvdrip.xvid.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    make_video(root / "Aliens.1986.1080p.mkv", "1920x800", "hevc", seconds=2)
    make_video(root / "Aliens.1986.720p.mkv", "1280x534", "h264", seconds=4)   # different running time
    make_video(root / "Arrival (2016)" / "Arrival (2016).mkv", "1920x1080", "h264")
    return root


@pytest.fixture(autouse=True)
def no_bitrate_floor(monkeypatch):
    # Test clips are tiny and compress far below real-world bitrates.
    monkeypatch.setattr(analyzer, "MIN_BITRATE_1080", 0)
    monkeypatch.setattr(analyzer, "MIN_BITRATE_2160", 0)


def plan_for(root):
    return build_plan(root, analyze(find_videos(root, min_size_mb=0)))


def by_name(plan, name):
    return next(i for i in plan.items if i.path.name == name)


def test_quality_grades(library):
    plan = plan_for(library)
    assert by_name(plan, "The.Dark.Knight.2008.1080p.BluRay.x264.mkv").info.tier == "good"
    assert by_name(plan, "Aliens.1986.1080p.mkv").info.resolution == "1080p"  # 1920x800 crop
    assert by_name(plan, "dark knight 2008 dvdrip.avi").info.tier == "poor"
    upgrade = {i.path.name for i in plan.needs_new_backup}
    assert upgrade == {"heat.dvdrip.xvid.avi"}   # Dark Knight has a good copy already


def test_duplicates(library):
    plan = plan_for(library)
    groups = {g.title: g for g in plan.duplicates}
    tdk = groups["The Dark Knight (2008)"]
    assert tdk.keep.path.suffix == ".mkv"
    assert [i.path.name for i in tdk.remove] == ["dark knight 2008 dvdrip.avi"]
    aliens = groups["Aliens (1986)"]
    assert aliens.remove == [] and len(aliens.review) == 1   # running times differ


def test_renames(library):
    plan = plan_for(library)
    targets = {r.item.path.name: r.target.name for r in plan.renames}
    assert targets["heat.dvdrip.xvid.avi"] == "Heat (1995).avi"
    assert targets["The.Dark.Knight.2008.1080p.BluRay.x264.mkv"] == "The Dark Knight (2008).mkv"
    assert "dark knight 2008 dvdrip.avi" not in targets      # it's being deleted
    assert "Arrival (2016).mkv" not in targets               # already correct
    # Two Aliens copies would collide on the same name; only one may be renamed.
    assert sum(1 for t in targets.values() if t == "Aliens (1986).mkv") == 1


def test_preview_changes_nothing(library, capsys):
    before = sorted(p.name for p in library.rglob("*"))
    main(["fix", str(library), "--rename", "--delete-duplicates", "--min-size", "0"])
    assert "PREVIEW ONLY" in capsys.readouterr().out
    assert sorted(p.name for p in library.rglob("*")) == before


def test_execute(library, tmp_path):
    root = tmp_path / "lib"
    shutil.copytree(library, root)
    trash = tmp_path / "trash"
    plan = plan_for(root)
    failures = apply_plan(plan, rename=True, delete=True, execute=True,
                          trash=trash, log_path=tmp_path / "log.csv")
    assert failures == 0
    assert (root / "The Dark Knight (2008)" / "The Dark Knight (2008).mkv").exists()
    assert not (root / "The Dark Knight (2008)" / "dark knight 2008 dvdrip.avi").exists()
    assert (trash / "The Dark Knight (2008)" / "dark knight 2008 dvdrip.avi").exists()
    assert (root / "Heat (1995)" / "Heat (1995).avi").exists()
    assert (root / "Heat (1995)" / "Heat (1995).en.srt").exists()      # subtitle followed
    assert "renamed" in (tmp_path / "log.csv").read_text()
