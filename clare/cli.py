"""clare — audit a movie backup library for quality, duplicates and naming."""

import argparse
import datetime
import shutil
import sys
from pathlib import Path

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from . import __version__
from .analyzer import RESOLUTION_HEIGHTS
from .fixer import apply_plan
from .library import DEFAULT_MIN_SIZE_MB, analyze, build_plan, find_videos
from .report import console, print_report, write_report


def _scan(args):
    root = args.path.expanduser().resolve()
    if not root.is_dir():
        console.print(f"[red]Not a folder:[/] {root}")
        sys.exit(2)
    if not shutil.which("ffprobe"):
        console.print("[red]ffprobe was not found.[/] Install FFmpeg (https://ffmpeg.org) "
                      "and make sure 'ffprobe' is on your PATH.")
        sys.exit(2)

    with console.status(f"Looking for video files in {root} …"):
        paths = find_videos(root, recursive=not args.no_recursive, min_size_mb=args.min_size)
    if not paths:
        console.print(f"[yellow]No video files found in {root}[/]")
        sys.exit(0)

    with Progress(TextColumn("Analyzing"), BarColumn(), MofNCompleteColumn(),
                  TimeRemainingColumn(), console=console, transient=True) as bar:
        task = bar.add_task("", total=len(paths))
        items = analyze(paths, workers=args.workers, progress=lambda: bar.advance(task))

    return build_plan(root, items, min_height=RESOLUTION_HEIGHTS[args.min_resolution])


def cmd_scan(args):
    plan = _scan(args)
    print_report(plan)
    if args.output:
        write_report(plan, args.output)
        console.print(f"Report saved to [bold]{args.output}[/]\n")


def cmd_fix(args):
    if not (args.rename or args.delete_duplicates):
        console.print("[yellow]Choose what to fix:[/] --rename and/or --delete-duplicates")
        sys.exit(2)

    plan = _scan(args)
    trash = args.trash.expanduser().resolve() if args.trash else None
    if trash and trash.is_relative_to(plan.root):
        console.print("[red]--trash must be outside the folder being scanned.[/]")
        sys.exit(2)

    if not args.execute:
        console.print("\n[bold yellow]PREVIEW ONLY — nothing will be changed.[/] "
                      "Add [bold]--execute[/] to apply.")
        apply_plan(plan, args.rename, args.delete_duplicates, execute=False, trash=trash)
        console.print()
        return

    n_del = len(plan.deletions) if args.delete_duplicates else 0
    n_ren = len(plan.renames) if args.rename else 0
    if n_del == 0 and n_ren == 0:
        console.print("\n[green]Nothing to change.[/]")
        return

    apply_plan(plan, args.rename, args.delete_duplicates, execute=False, trash=trash)
    what = []
    if n_del:
        what.append(f"{'move' if trash else 'PERMANENTLY DELETE'} {n_del} file(s)")
    if n_ren:
        what.append(f"rename {n_ren} file(s)")
    console.print(f"\n[bold red]About to {' and '.join(what)}.[/]")
    if not args.yes:
        answer = console.input("Type [bold]yes[/] to continue: ")
        if answer.strip().lower() != "yes":
            console.print("Cancelled — nothing was changed.")
            return

    log_path = args.log or Path(f"clare-changes-{datetime.datetime.now():%Y%m%d-%H%M%S}.csv")
    failures = apply_plan(plan, args.rename, args.delete_duplicates, execute=True,
                          trash=trash, log_path=log_path.resolve())
    console.print(f"\n[bold green]Done.[/]" + (f" [red]{failures} change(s) failed.[/]" if failures else ""))
    sys.exit(1 if failures else 0)


def build_parser():
    p = argparse.ArgumentParser(
        prog="clare",
        description="Check a movie backup library for low-quality copies, duplicates and bad file names.")
    p.add_argument("--version", action="version", version=f"clare {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("path", type=Path, help="folder to scan (e.g. /volume1/Movies or Z:\\Movies)")
        sp.add_argument("--min-resolution", choices=["480p", "720p", "1080p", "2160p"], default="1080p",
                        help="lowest resolution you consider acceptable (default: 1080p)")
        sp.add_argument("--no-recursive", action="store_true", help="don't look in subfolders")
        sp.add_argument("--min-size", type=float, default=DEFAULT_MIN_SIZE_MB, metavar="MB",
                        help=f"ignore videos smaller than this, e.g. trailers (default: {DEFAULT_MIN_SIZE_MB})")
        sp.add_argument("--workers", type=int, default=4,
                        help="files to analyze at once (default: 4)")

    scan = sub.add_parser("scan", help="report problems without changing anything")
    common(scan)
    scan.add_argument("-o", "--output", type=Path, metavar="FILE",
                      help="also save a full per-file report (.csv for Excel, or .json)")
    scan.set_defaults(func=cmd_scan)

    fix = sub.add_parser("fix", help="rename files and/or remove inferior duplicates (preview by default)")
    common(fix)
    fix.add_argument("--rename", action="store_true", help="rename files to 'Title (Year).ext'")
    fix.add_argument("--delete-duplicates", action="store_true",
                     help="remove lower-quality copies when a better copy of the same movie exists")
    fix.add_argument("--trash", type=Path, metavar="FOLDER",
                     help="move removed duplicates here instead of deleting them")
    fix.add_argument("--execute", action="store_true", help="actually make the changes")
    fix.add_argument("--yes", action="store_true", help="don't ask for confirmation (with --execute)")
    fix.add_argument("--log", type=Path, metavar="FILE",
                     help="where to record changes (default: clare-changes-<time>.csv)")
    fix.set_defaults(func=cmd_fix)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
