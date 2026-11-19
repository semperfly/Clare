# Clare

Clare checks a movie backup library and tells you:

- **Which movies need a new backup.** These are movies where the best copy you have is below standard: SD/DVD resolution, an obsolete codec (DivX/Xvid, WMV, RealVideo…) or a very low bitrate.
- **Which files are duplicates.** It picks the best copy of each movie and marks the others for removal.
- **Which files are badly named.** It suggests a [Plex/Jellyfin-style](https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/) name: `Movie Title (Year).ext`.

When you tell it to, it renames files and removes the inferior duplicates. It never changes anything unless you pass `--execute`.

## Install

You need **Python 3.9+** and **FFmpeg**. Clare uses FFmpeg's `ffprobe` to read video details.

| System | FFmpeg |
|---|---|
| Windows | `winget install ffmpeg` |
| macOS | `brew install ffmpeg` |
| Linux / NAS shell | `sudo apt install ffmpeg` (or your distro's equivalent) |

Then, from this folder:

```bash
pip install .
```

You can run Clare on the NAS itself if it has Python, or on any computer that has the share mounted, e.g. `Z:\Movies` or `/Volumes/Movies`.

## 1. Get a report (changes nothing)

```bash
clare scan /Volumes/Movies
clare scan Z:\Movies -o report.csv        # also save a spreadsheet with one row per file
```

The report has these sections:

| Section | Meaning |
|---|---|
| **Needs a new backup** | The best copy of this movie is below standard. Re-rip it. |
| **Could not be read** | `ffprobe` couldn't open the file. It may be corrupt, so check that backup. |
| **Duplicate copies** | **KEEP** is the best copy. **DELETE** will be removed. **REVIEW** won't be touched (see below). |
| **Renames** | Files that will get a clean name. |
| **Names to fix by hand** | No year was found, or the new name would clash with another file. |

### What counts as acceptable

The default minimum is **1080p** with a modern codec (H.264, H.265/HEVC, AV1, VP9, VC-1) at a sensible bitrate:

| Grade | Meaning |
|---|---|
| EXCELLENT | 4K with a modern codec and ≥10 Mbps |
| GOOD | 1080p with a modern codec and ≥3 Mbps |
| FAIR | 720p, or 1080p with a low bitrate or an unusual codec |
| POOR | Below 720p, an obsolete codec, or unreadable |

Widescreen films that are cropped (e.g. 1920×800) still count as 1080p. To use a different bar, pass `--min-resolution 720p` or `--min-resolution 2160p`.

## 2. Preview the fixes

```bash
clare fix /Volumes/Movies --rename --delete-duplicates
```

This prints exactly what would happen. Nothing is changed.

## 3. Apply them

```bash
# Safest: move removed duplicates to a holding folder instead of deleting them
clare fix /Volumes/Movies --rename --delete-duplicates --trash /Volumes/Other/clare-trash --execute

# Or delete them permanently
clare fix /Volumes/Movies --rename --delete-duplicates --execute
```

You'll be asked to type `yes` first. Every rename, move and delete is written to a `clare-changes-<time>.csv` log, so you can see or undo what happened.

### Safety rules

- A duplicate is only removed when a better copy of **the same movie and edition** exists.
- Copies whose running times differ by more than 5% are never auto-deleted (they could be different cuts). They show as **REVIEW**.
- If any copy in a group can't be read, nothing in that group is deleted.
- Editions are kept apart, e.g. `Aliens (1986)` and `Aliens (1986) {edition-Special Edition}` are not treated as duplicates.
- Renames never overwrite an existing file. Subtitles and other files named after the video (`Movie.en.srt`, `Movie.nfo`) are renamed with it.
- Files under 50 MB (trailers, samples) are ignored. Change this with `--min-size`. Hidden folders and Synology `@eaDir` folders are skipped.

## Options

```
clare scan PATH [--min-resolution 720p|1080p|2160p] [-o report.csv|report.json]
clare fix  PATH [--rename] [--delete-duplicates] [--trash FOLDER] [--execute] [--yes] [--log FILE]

Common:  --no-recursive   --min-size MB   --workers N   (files analysed in parallel, default 4)
```

## Development

```bash
pip install -e '.[test]'
pytest            # the end-to-end tests generate real video files with ffmpeg
```
