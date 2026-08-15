"""Terminal (rich) and markdown rendering for the queue and explain views."""

import json
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

REFERRAL_WARNING = (
    "⚠  REFERRAL_HOLD — you have a referral at this company. "
    "Do NOT apply through the cold link below. Route through your contact first; "
    "applying cold would burn the referral irreversibly."
)


def _host(url: str) -> str:
    return urlparse(url or "").netloc.removeprefix("www.")


def _age(score) -> str:
    return f"{score.age_days:.1f}d"


def queue_table(scored: list, console: Console) -> None:
    holds = [(r, s, src) for r, s, src in scored if r["referral_hold"]]
    if holds:
        names = ", ".join(sorted({r["company"] for r, _, _ in holds}))
        console.print(Panel(REFERRAL_WARNING + f"\n\nHeld companies in this queue: [bold]{names}[/bold]",
                            style="bold red", title="REFERRAL HOLD"))
    table = Table(title="intern-queue — ranked queue", show_lines=False)
    for col in ("id", "score", "flag", "company", "title", "location", "age", "resume", "sources", "apply via"):
        table.add_column(col)
    for row, score, sources in scored:
        flag = "[bold red]REFERRAL_HOLD[/bold red]" if row["referral_hold"] else row["status"]
        locs = json.loads(row["locations"])
        table.add_row(
            str(row["id"]), f"{score.total:.3f}", flag, row["company"],
            row["title"][:60], (locs[0] if locs else "?") + (f" +{len(locs) - 1}" if len(locs) > 1 else ""),
            _age(score), row["resume_version"], sources, _host(row["apply_url"]),
        )
    console.print(table)


def queue_markdown(scored: list) -> str:
    lines = ["# intern-queue", ""]
    if any(r["referral_hold"] for r, _, _ in scored):
        lines += ["> **⚠ REFERRAL_HOLD rows are pinned on top. Do not use their apply links — route through your referral.**", ""]
    lines += ["| id | score | flag | company | title | location | resume | sources | apply |",
              "|---|---|---|---|---|---|---|---|---|"]
    for row, score, sources in scored:
        flag = "**REFERRAL_HOLD**" if row["referral_hold"] else row["status"]
        locs = json.loads(row["locations"])
        apply = "(referral — no cold link)" if row["referral_hold"] else f"[Apply]({row['apply_url']})"
        lines.append(
            f"| {row['id']} | {score.total:.3f} | {flag} | {row['company']} | {row['title']} "
            f"| {locs[0] if locs else '?'} | {row['resume_version']} | {sources} | {apply} |"
        )
    return "\n".join(lines) + "\n"


def explain_view(row, score, sources: str, console: Console) -> None:
    table = Table(title=f"score breakdown — [{row['id']}] {row['company']}: {row['title']}")
    for col in ("factor", "value", "why"):
        table.add_column(col)
    table.add_row("tier_weight", f"{score.tier_weight:.2f}",
                  "matched company " + repr(score.tier_source) if score.tier_source != "default"
                  else "not in [companies] — default_tier")
    fits = ", ".join(f"{p!r} {w:+.2f}" for p, w in score.role_matches) or "no patterns matched (base only)"
    table.add_row("role_fit", f"{score.role_fit:.2f}", fits)
    table.add_row("recency", f"{score.recency:.3f}",
                  f"first seen {score.age_days:.1f}d ago (half-life 3d, floored)")
    table.add_row("ats_friction", f"{score.ats:.2f}", score.ats_reason)
    table.add_row("[bold]total[/bold]", f"[bold]{score.total:.3f}[/bold]",
                  "tier × role_fit × recency × ats")
    console.print(table)
    console.print(f"status={row['status']}  sources={sources}  resume={row['resume_version']}  apply={row['apply_url']}")
    if row["referral_hold"]:
        console.print(Panel(REFERRAL_WARNING, style="bold red"))
    if row["note"]:
        console.print(f"enrichment note: {row['note']}")
    if row["knockouts"] and json.loads(row["knockouts"]):
        console.print(f"[red]enrichment knockouts: {', '.join(json.loads(row['knockouts']))}[/red]")
    if row["escalations"] and json.loads(row["escalations"]):
        console.print("[yellow]escalations (answer these yourself):[/yellow]")
        for q in json.loads(row["escalations"]):
            console.print(f"  • {q}")
