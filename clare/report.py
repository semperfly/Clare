"""Terminal, CSV and JSON reports."""

import csv
import json
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analyzer import EXCELLENT, FAIR, GOOD, POOR
from .library import Item, Plan

console = Console()

TIER_STYLE = {EXCELLENT: "bright_green", GOOD: "green", FAIR: "yellow", POOR: "red"}


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_minutes(seconds) -> str:
    return f"{seconds / 60:.0f} min" if seconds else "?"


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def describe(item: Item) -> str:
    info = item.info
    if info.error:
        return "unreadable"
    v = info.video
    parts = [info.resolution, v.codec if v else "?"]
    if info.bitrate_mbps:
        parts.append(f"{info.bitrate_mbps:.1f} Mbps")
    if v and v.hdr:
        parts.append("HDR")
    parts += [fmt_size(info.size), fmt_minutes(info.duration)]
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

def print_report(plan: Plan) -> None:
    root = plan.root
    upgrade = plan.needs_new_backup
    reviews = [g for g in plan.duplicates if g.review]

    console.print()
    console.rule("[bold]Clare — Media Library Report")
    console.print(f"[dim]{root}   ·   minimum standard: {plan.min_height}p[/dim]\n")

    summary = Table.grid(padding=(0, 3))
    summary.add_row("Video files scanned", f"[bold]{len(plan.items)}")
    summary.add_row("Movies needing a new backup", f"[bold red]{len(upgrade)}")
    summary.add_row("Duplicate copies to delete",
                    f"[bold magenta]{len(plan.deletions)}[/]  "
                    f"[dim]({fmt_size(sum(i.info.size for i in plan.deletions))} reclaimable)")
    summary.add_row("Duplicates needing your review", f"[bold yellow]{sum(len(g.review) for g in reviews)}")
    summary.add_row("Files to rename", f"[bold cyan]{len(plan.renames)}")
    summary.add_row("Names needing manual fixes", f"[bold yellow]{len(plan.skipped_renames)}")
    if plan.errors:
        summary.add_row("Unreadable / corrupt files", f"[bold red]{len(plan.errors)}")
    console.print(Panel(summary, title="Summary", expand=False))

    if upgrade:
        console.print("\n[bold red]▸ Needs a new backup[/]  [dim](best copy you have is below standard)[/]")
        t = Table(box=box.SIMPLE_HEAD, header_style="bold")
        t.add_column("File", overflow="fold", ratio=3)
        t.add_column("Grade", no_wrap=True)
        t.add_column("Details", overflow="fold", ratio=2)
        t.add_column("Why", overflow="fold", ratio=3)
        for it in upgrade:
            t.add_row(rel(it.path, root),
                      Text(it.info.tier.upper(), style=TIER_STYLE[it.info.tier]),
                      describe(it), "; ".join(it.info.issues) or "below minimum resolution")
        console.print(t)

    if plan.errors:
        console.print("\n[bold red]▸ Could not be read[/]  [dim](possibly corrupt — check these backups)[/]")
        for it in plan.errors:
            console.print(f"  {rel(it.path, root)}  [dim]{it.info.error}[/]")

    if plan.duplicates:
        console.print("\n[bold magenta]▸ Duplicate copies[/]")
        for g in plan.duplicates:
            console.print(f"  [bold]{g.title}[/]")
            console.print(f"    [green]KEEP  [/]  {rel(g.keep.path, root)}  [dim]{describe(g.keep)}[/]")
            for it in g.remove:
                console.print(f"    [red]DELETE[/]  {rel(it.path, root)}  [dim]{describe(it)}[/]")
            for it in g.review:
                console.print(f"    [yellow]REVIEW[/]  {rel(it.path, root)}  [dim]{describe(it)}[/]")
            if g.review:
                console.print(f"            [yellow]not deleted automatically: {g.review_reason}[/]")

    if plan.renames:
        console.print("\n[bold cyan]▸ Renames[/]")
        t = Table(box=box.SIMPLE_HEAD, header_style="bold")
        t.add_column("Folder", overflow="fold", style="dim")
        t.add_column("Current name", overflow="fold")
        t.add_column("New name", overflow="fold", style="cyan")
        for r in plan.renames:
            t.add_row(rel(r.item.path.parent, root), r.item.path.name, r.target.name)
        console.print(t)

    if plan.skipped_renames:
        console.print("\n[bold yellow]▸ Names to fix by hand[/]")
        for s in plan.skipped_renames:
            console.print(f"  {rel(s.item.path, root)}  [dim]{s.reason}[/]")

    if not (upgrade or plan.duplicates or plan.renames or plan.skipped_renames or plan.errors):
        console.print("\n[bold green]No problems found.[/]")
    elif plan.deletions or plan.renames:
        console.print("\n[dim]Nothing has been changed. To preview the fixes run "
                      "[bold]clare fix PATH --rename --delete-duplicates[/bold]; "
                      "add [bold]--execute[/bold] to apply them.[/]")
    console.print()


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _rows(plan: Plan):
    deleting = {id(i): g for g in plan.duplicates for i in g.remove}
    reviewing = {id(i): g for g in plan.duplicates for i in g.review}
    keeping = {id(g.keep): g for g in plan.duplicates}
    renames = {id(r.item): r for r in plan.renames}
    skipped = {id(s.item): s for s in plan.skipped_renames}
    upgrade = {id(i) for i in plan.needs_new_backup}

    for it in plan.items:
        info, v = it.info, it.info.video
        if id(it) in deleting:
            dup = "delete (inferior duplicate)"
        elif id(it) in reviewing:
            dup = f"review: {reviewing[id(it)].review_reason}"
        elif id(it) in keeping:
            dup = "keep (best copy)"
        else:
            dup = ""
        yield {
            "path": str(it.path),
            "title": it.name.title,
            "year": it.name.year,
            "edition": it.name.edition,
            "grade": info.tier,
            "acceptable": info.is_acceptable(plan.min_height),
            "needs_new_backup": id(it) in upgrade,
            "resolution": info.resolution,
            "width": v.width if v else None,
            "height": v.height if v else None,
            "codec": v.codec if v else None,
            "bitrate_mbps": round(info.bitrate_mbps, 2) if info.bitrate_mbps else None,
            "hdr": bool(v and v.hdr),
            "duration_min": round(info.duration / 60, 1) if info.duration else None,
            "size_gb": round(info.size / 1024 ** 3, 2),
            "issues": "; ".join(info.issues),
            "duplicate_action": dup,
            "rename_to": renames[id(it)].target.name if id(it) in renames else "",
            "name_problem": skipped[id(it)].reason if id(it) in skipped else "",
        }


def write_report(plan: Plan, out: Path) -> None:
    rows = list(_rows(plan))
    if out.suffix.lower() == ".json":
        out.write_text(json.dumps({"root": str(plan.root), "min_height": plan.min_height,
                                   "files": rows}, indent=2), encoding="utf-8")
    else:
        with out.open("w", newline="", encoding="utf-8-sig") as f:   # BOM so Excel reads UTF-8
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["path"])
            w.writeheader()
            w.writerows(rows)
