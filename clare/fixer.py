"""Carry out a Plan: delete (or move aside) inferior duplicates, then rename."""

import csv
import datetime
import shutil
from pathlib import Path
from typing import Optional

from .library import Plan
from .report import console, describe, fmt_size, rel


def apply_plan(plan: Plan, rename: bool, delete: bool, execute: bool,
               trash: Optional[Path] = None, log_path: Optional[Path] = None) -> int:
    """Print (and, if *execute*, perform) the planned changes. Returns failure count."""
    root = plan.root
    log_rows = []
    failures = 0
    verb = "" if execute else "would "

    def log(action, src, dst=""):
        log_rows.append({"time": datetime.datetime.now().isoformat(timespec="seconds"),
                         "action": action, "from": str(src), "to": str(dst)})

    if delete:
        console.print(f"\n[bold]Duplicates — {verb}{'move' if trash else 'delete'} "
                      f"{len(plan.deletions)} file(s), "
                      f"{fmt_size(sum(i.info.size for i in plan.deletions))}[/]")
        for g in plan.duplicates:
            for it in g.remove:
                console.print(f"  [red]{'MOVE  ' if trash else 'DELETE'}[/] {rel(it.path, root)}  "
                              f"[dim]{describe(it)} — keeping {g.keep.path.name}[/]")
                if not execute:
                    continue
                try:
                    if trash:
                        dest = trash / rel(it.path, root)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(it.path), str(dest))
                        log("moved", it.path, dest)
                    else:
                        it.path.unlink()
                        log("deleted", it.path)
                except OSError as exc:
                    failures += 1
                    console.print(f"    [bold red]failed:[/] {exc}")
            for it in g.review:
                console.print(f"  [yellow]SKIP  [/] {rel(it.path, root)}  [dim]{g.review_reason}[/]")

    if rename:
        console.print(f"\n[bold]Renames — {verb}rename {len(plan.renames)} file(s)[/]")
        for r in plan.renames:
            console.print(f"  [cyan]RENAME[/] {rel(r.item.path, root)}\n"
                          f"         → {r.target.name}")
            for old, new in r.companions:
                console.print(f"         → {new.name} [dim](was {old.name})[/]")
            if not execute:
                continue
            try:
                r.item.path.rename(r.target)
                log("renamed", r.item.path, r.target)
                for old, new in r.companions:
                    if not new.exists():
                        old.rename(new)
                        log("renamed", old, new)
            except OSError as exc:
                failures += 1
                console.print(f"    [bold red]failed:[/] {exc}")
        for s in plan.skipped_renames:
            console.print(f"  [yellow]SKIP  [/] {rel(s.item.path, root)}  [dim]{s.reason}[/]")

    if execute and log_rows and log_path:
        new = not log_path.exists()
        with log_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["time", "action", "from", "to"])
            if new:
                w.writeheader()
            w.writerows(log_rows)
        console.print(f"\n[dim]Every change was recorded in {log_path}[/]")
    return failures
