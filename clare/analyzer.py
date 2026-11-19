"""Probe video files with ffprobe and grade their quality."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

VIDEO_EXTENSIONS = frozenset({
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv",
    ".ts", ".m2ts", ".mpg", ".mpeg", ".vob", ".divx", ".rmvb", ".webm",
})

# ffprobe codec_name values.
MODERN_CODECS = frozenset({"h264", "hevc", "av1", "vp9", "vc1"})
# Codecs that are inefficient or obsolete for HD playback.
OLD_CODECS = frozenset({
    "mpeg1video",
    "mpeg4",                       # MPEG-4 Part 2: DivX / Xvid
    "msmpeg4v1", "msmpeg4v2", "msmpeg4v3", "msmpeg4",
    "wmv1", "wmv2", "wmv3",
    "rv10", "rv20", "rv30", "rv40",
    "flv1", "h263", "vp6", "vp6f", "vp8",
    "svq1", "svq3", "cinepak",
    "indeo2", "indeo3", "indeo4", "indeo5", "theora",
})
# mpeg2video is deliberately in neither set: at 480p (DVD rips) the resolution
# rule already fails it, and at 1080 it is a Blu-ray/broadcast remux, which is fine.

EXCELLENT, GOOD, FAIR, POOR = "excellent", "good", "fair", "poor"
TIER_RANK = {EXCELLENT: 3, GOOD: 2, FAIR: 1, POOR: 0}

# Minimum video bitrate (Mbps) before a file is marked down a tier.
MIN_BITRATE_1080 = 3.0
MIN_BITRATE_2160 = 10.0

RESOLUTION_HEIGHTS = {"480p": 480, "720p": 720, "1080p": 1080, "2160p": 2160, "4k": 2160}


@dataclass
class VideoStream:
    codec: str
    codec_long: str
    width: int
    height: int
    bitrate: Optional[int]       # bits per second
    hdr: bool


@dataclass
class MediaInfo:
    path: Path
    size: int
    duration: Optional[float]
    video: Optional[VideoStream]
    audio_tracks: int
    error: Optional[str] = None

    # ---- derived ---------------------------------------------------------

    @property
    def effective_height(self) -> int:
        """Height, treating cropped widescreen (e.g. 1920x800) as its full class."""
        if not self.video:
            return 0
        v = self.video
        # Widescreen films are often cropped vertically; use width to classify.
        if v.width >= 3800:
            return max(v.height, 2160)
        if v.width >= 1900:
            return max(v.height, 1080)
        if v.width >= 1250:
            return max(v.height, 720)
        return v.height

    @property
    def resolution(self) -> str:
        if not self.video:
            return "unknown"
        h = self.effective_height
        for label, minimum in (("4K", 2160), ("1440p", 1440), ("1080p", 1080),
                               ("720p", 720), ("480p", 480)):
            if h >= minimum:
                return label
        return f"{h}p"

    @property
    def bitrate_mbps(self) -> Optional[float]:
        if self.video and self.video.bitrate:
            return self.video.bitrate / 1_000_000
        return None

    @property
    def tier(self) -> str:
        if self.error or not self.video:
            return POOR
        codec = self.video.codec.lower()
        h = self.effective_height
        if codec in OLD_CODECS or h < 720:
            return POOR
        if h < 1080:
            return FAIR

        tier = EXCELLENT if h >= 2160 else GOOD
        if codec not in MODERN_CODECS and codec != "mpeg2video":
            tier = FAIR
        mbps = self.bitrate_mbps
        if mbps is not None:
            floor = MIN_BITRATE_2160 if h >= 2160 else MIN_BITRATE_1080
            if mbps < floor:
                tier = GOOD if tier == EXCELLENT else FAIR
        return tier

    def is_acceptable(self, min_height: int = 1080) -> bool:
        """True if the file meets the minimum resolution and isn't flagged as poor/fair."""
        if self.effective_height < min_height:
            return False
        if min_height >= 1080:
            return self.tier in (GOOD, EXCELLENT)
        return self.tier != POOR

    @property
    def issues(self) -> List[str]:
        if self.error:
            return [f"Could not analyze: {self.error}"]
        if not self.video:
            return ["No video stream found"]

        out: List[str] = []
        v = self.video
        codec = v.codec.lower()
        h = self.effective_height

        if h < 720:
            out.append(f"SD resolution ({v.width}x{v.height})")
        elif h < 1080:
            out.append(f"720p — below 1080p ({v.width}x{v.height})")

        if codec in OLD_CODECS:
            out.append(f"Outdated codec: {v.codec_long}")
        elif codec not in MODERN_CODECS and codec != "mpeg2video":
            out.append(f"Unusual codec: {codec}")

        mbps = self.bitrate_mbps
        if mbps is not None and h >= 1080:
            floor = MIN_BITRATE_2160 if h >= 2160 else MIN_BITRATE_1080
            if mbps < floor:
                out.append(f"Low bitrate: {mbps:.1f} Mbps (expect ≥{floor:.0f} at {self.resolution})")
        return out


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe_file(path: Path) -> MediaInfo:
    """Run ffprobe on *path*; never raises — failures are recorded in ``error``."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return MediaInfo(path, 0, None, None, 0, error=str(exc))

    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return MediaInfo(path, size, None, None, 0, error="ffprobe not found — install FFmpeg")
    except subprocess.TimeoutExpired:
        return MediaInfo(path, size, None, None, 0, error="ffprobe timed out")

    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        msg = lines[-1].replace(f"{path}: ", "") if lines else "ffprobe failed (file may be corrupt)"
        return MediaInfo(path, size, None, None, 0, error=msg)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return MediaInfo(path, size, None, None, 0, error="unreadable ffprobe output")

    fmt = data.get("format", {})
    video: Optional[VideoStream] = None
    audio = 0
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        if kind == "audio":
            audio += 1
        elif kind == "video" and video is None:
            if s.get("disposition", {}).get("attached_pic"):
                continue  # embedded cover art, not the movie
            transfer = (s.get("color_transfer") or "").lower()
            bitrate = _int(s.get("bit_rate")) or _int(s.get("tags", {}).get("BPS"))
            video = VideoStream(
                codec=s.get("codec_name", "unknown"),
                codec_long=s.get("codec_long_name", s.get("codec_name", "unknown")),
                width=_int(s.get("width")) or 0,
                height=_int(s.get("height")) or 0,
                bitrate=bitrate,
                hdr=transfer in ("smpte2084", "arib-std-b67"),
            )

    # Fall back to the container's overall bitrate (slightly overstates video).
    if video and video.bitrate is None:
        video.bitrate = _int(fmt.get("bit_rate"))

    duration = None
    try:
        duration = float(fmt["duration"])
    except (KeyError, TypeError, ValueError):
        pass

    return MediaInfo(path, size, duration, video, audio)
