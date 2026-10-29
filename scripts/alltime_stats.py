#!/usr/bin/env python3
"""All-time git stats from raw history -> SVG card.

  python scripts/alltime_stats.py --discover   # list author identities
  python scripts/alltime_stats.py              # write all-time-stats.svg

Env: OWNER, AUTHORS (git -P regex), GH_TOKEN. Flags: --cache, --no-fetch, --out.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone

DEFAULT_OWNER = "Cukrus-directive"

# username + noreply, and the personal gmail most history was authored under
DEFAULT_AUTHORS = r"Cukrus-directive|mail4acc@gmail\.com"

EXCLUDE_REPOS: set[str] = set()

EXT_LANG = {
    ".py": "Python", ".ipynb": "Jupyter Notebook",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".ps1": "PowerShell", ".psm1": "PowerShell", ".psd1": "PowerShell",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".bat": "Batchfile", ".cmd": "Batchfile",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".cs": "C#", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".scala": "Scala",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "Sass",
    ".vue": "Vue", ".svelte": "Svelte",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".xml": "XML", ".md": "Markdown", ".rst": "reStructuredText",
    ".sql": "SQL", ".r": "R", ".lua": "Lua", ".pl": "Perl", ".dart": "Dart",
    ".tf": "HCL", ".bicep": "Bicep", ".dockerfile": "Dockerfile",
}
LANG_COLOR = {
    "Python": "#3572A5", "Jupyter Notebook": "#DA5B0B", "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6", "PowerShell": "#012456", "Shell": "#89e051",
    "Batchfile": "#C1F12E", "C": "#555555", "C++": "#f34b7d", "C#": "#178600",
    "Go": "#00ADD8", "Rust": "#dea584", "Java": "#b07219", "Kotlin": "#A97BFF",
    "Ruby": "#701516", "PHP": "#4F5D95", "Swift": "#F05138", "Scala": "#c22d40",
    "HTML": "#e34c26", "CSS": "#563d7c", "SCSS": "#c6538c", "Sass": "#a53b70",
    "Vue": "#41b883", "Svelte": "#ff3e00", "JSON": "#292929", "YAML": "#cb171e",
    "TOML": "#9c4221", "XML": "#0060ac", "Markdown": "#083fa1", "SQL": "#e38c00",
    "R": "#198CE7", "Lua": "#000080", "Perl": "#0298c3", "Dart": "#00B4AB",
    "HCL": "#844FBA", "Bicep": "#519aba", "Dockerfile": "#384d54",
    "reStructuredText": "#141414", "Other": "#ededed",
}

LANG_IGNORE_FOR_CHART = {"JSON", "Markdown", "YAML", "XML", "HTML", "CSS", "TOML",
                         "reStructuredText"}


def run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, encoding="utf-8",
                       errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr}")
    return r


def list_repos(owner):
    r = run(["gh", "repo", "list", owner, "--limit", "200",
             "--json", "nameWithOwner,isPrivate,isFork",
             "-q", '.[] | [.nameWithOwner, (.isPrivate|tostring), (.isFork|tostring)] | @tsv'])
    repos = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        name, priv, fork = line.split("\t")
        if name in EXCLUDE_REPOS:
            continue
        repos.append({"name": name, "private": priv == "true", "fork": fork == "true"})
    return repos


def clone_url(name):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return f"https://x-access-token:{token}@github.com/{name}.git"
    return f"https://github.com/{name}.git"


def ensure_bare(name, cache, fetch=True):
    safe = name.replace("/", "__") + ".git"
    path = os.path.join(cache, safe)
    if os.path.isdir(path):
        if fetch:
            run(["git", "-C", path, "fetch", "--quiet", "--all", "--tags", "--prune"],
                check=False)
        return path
    if not fetch:
        return None
    r = run(["git", "clone", "--quiet", "--bare", clone_url(name), path], check=False)
    if r.returncode != 0:
        print(f"  ! clone failed for {name}: {r.stderr.strip()[:160]}", file=sys.stderr)
        return None
    return path


def discover(repos, cache, fetch):
    counts = defaultdict(int)
    for repo in repos:
        path = ensure_bare(repo["name"], cache, fetch)
        if not path:
            continue
        r = run(["git", "-C", path, "log", "--all", "--pretty=format:%an <%ae>"],
                check=False)
        for line in r.stdout.splitlines():
            counts[line.strip()] += 1
    print(f"\n{'commits':>8}  author identity")
    print("-" * 60)
    for ident, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{n:>8}  {ident}")


def ext_to_lang(path):
    lower = path.lower()
    if lower.endswith("dockerfile") or "/dockerfile" in lower:
        return "Dockerfile"
    dot = lower.rfind(".")
    if dot == -1:
        return None
    return EXT_LANG.get(lower[dot:])


def collect(repos, authors, cache, fetch):
    totals = {"commits": 0, "added": 0, "removed": 0, "files": 0}
    lang_lines = defaultdict(int)
    year_counts = defaultdict(int)
    first = last = None
    per_repo = []
    reached = 0

    for repo in repos:
        name = repo["name"]
        path = ensure_bare(name, cache, fetch)
        if not path:
            per_repo.append({"name": name, "commits": 0, "reached": False})
            continue
        reached += 1

        rc = run(["git", "-C", path, "log", "--all", "--no-merges",
                  "-P", f"--author={authors}", "--pretty=format:%H|%ad",
                  "--date=format:%Y-%m-%d"], check=False)
        seen = set()
        for line in rc.stdout.splitlines():
            if "|" not in line:
                continue
            h, day = line.split("|", 1)
            if h in seen:
                continue
            seen.add(h)
            year_counts[int(day[:4])] += 1
            first = day if first is None or day < first else first
            last = day if last is None or day > last else last
        commits = len(seen)

        rn = run(["git", "-C", path, "log", "--all", "--no-merges",
                  "-P", f"--author={authors}", "--pretty=tformat:",
                  "--numstat"], check=False)
        added = removed = files = 0
        for line in rn.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, p = parts
            ai = int(a) if a.isdigit() else 0
            di = int(d) if d.isdigit() else 0
            added += ai
            removed += di
            files += 1
            lang = ext_to_lang(p)
            if lang:
                lang_lines[lang] += ai + di

        totals["commits"] += commits
        totals["added"] += added
        totals["removed"] += removed
        totals["files"] += files
        per_repo.append({"name": name, "commits": commits, "added": added,
                         "removed": removed, "reached": True})

    return totals, lang_lines, year_counts, (first, last), per_repo, reached


def human(n):
    n = int(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n/1_000:.1f}k".replace(".0k", "k")
    return str(n)


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


HEAT = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]


def heat_level(n, peak):
    if n <= 0 or peak <= 0:
        return 0
    return max(1, min(4, -(-4 * n // peak)))


def fmt_day(day):
    if not day:
        return "n/a"
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{d.day} {d:%b %Y}"


def render_svg(lang_lines, year_counts, meta, out):
    W = 360
    chart = {k: v for k, v in lang_lines.items() if k not in LANG_IGNORE_FOR_CHART}
    total_lang = sum(chart.values()) or 1
    top = sorted(chart.items(), key=lambda kv: -kv[1])[:6]

    parts = [f'<text x="0" y="11">{meta["first"]} – {meta["last"]}</text>']
    y = 22

    # one cell per year, shaded relative to the busiest year
    years = list(range(meta["year_lo"], meta["year_hi"] + 1))
    peak = max(year_counts.values(), default=0)
    gap = 3
    cellw = (W - (len(years) - 1) * gap) / len(years)
    cellh = 14
    for i, yr in enumerate(years):
        cx = i * (cellw + gap)
        fill = HEAT[heat_level(year_counts.get(yr, 0), peak)]
        parts.append(f'<rect x="{cx:.1f}" y="{y}" width="{cellw:.1f}" height="{cellh}" '
                     f'rx="2" fill="{fill}"/>'
                     f'<text x="{cx + cellw / 2:.1f}" y="{y + cellh + 12}" '
                     f'text-anchor="middle">{yr}</text>')
    y += cellh + 26

    if top:
        x = 0
        for lang, v in top:
            seg = W * v / total_lang
            color = LANG_COLOR.get(lang, LANG_COLOR["Other"])
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{max(seg, 0.5):.1f}" '
                         f'height="6" fill="{color}"/>')
            x += seg
        y += 22

        col_w = W / 3
        for i, (lang, v) in enumerate(top):
            lx = (i % 3) * col_w
            ly = y + (i // 3) * 17
            color = LANG_COLOR.get(lang, LANG_COLOR["Other"])
            parts.append(f'<circle cx="{lx + 4:.0f}" cy="{ly - 4}" r="4" fill="{color}"/>'
                         f'<text x="{lx + 12:.0f}" y="{ly}">{esc(lang)} '
                         f'{100 * v / total_lang:.0f}%</text>')
        y += ((len(top) - 1) // 3) * 17

    H = y + 4

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="\'Segoe UI\', Ubuntu, Helvetica, Arial, '
           f'sans-serif" font-size="11" fill="#8b949e">{"".join(parts)}</svg>\n')
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {out} ({W}x{H})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--owner", default=os.environ.get("OWNER", DEFAULT_OWNER))
    ap.add_argument("--authors", default=os.environ.get("AUTHORS", DEFAULT_AUTHORS))
    ap.add_argument("--cache", default=os.environ.get("STATS_CACHE",
                    os.path.join(tempfile.gettempdir(), "alltime-stats-cache")))
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--out", default="all-time-stats.svg")
    args = ap.parse_args()

    os.makedirs(args.cache, exist_ok=True)
    fetch = not args.no_fetch

    print(f"owner={args.owner}  cache={args.cache}  fetch={fetch}", file=sys.stderr)
    repos = list_repos(args.owner)
    print(f"enumerated {len(repos)} repos "
          f"({sum(r['private'] for r in repos)} private, "
          f"{sum(r['fork'] for r in repos)} forks)", file=sys.stderr)

    if args.discover:
        discover(repos, args.cache, fetch)
        return

    totals, lang_lines, year_counts, (first, last), per_repo, reached = collect(
        repos, args.authors, args.cache, fetch)

    print("\n=== per-repo (commits by identity) ===", file=sys.stderr)
    for r in sorted(per_repo, key=lambda x: -x.get("commits", 0)):
        if r.get("commits"):
            print(f"  {r['commits']:>5}  +{r.get('added',0):<7} -{r.get('removed',0):<7} {r['name']}",
                  file=sys.stderr)
    print(f"\nTOTALS: {totals}", file=sys.stderr)

    year_now = datetime.now(timezone.utc).year
    meta = {
        "first": fmt_day(first),
        "last": fmt_day(last),
        "year_lo": min(year_counts, default=year_now),
        "year_hi": max(max(year_counts, default=year_now), year_now),
    }
    render_svg(lang_lines, year_counts, meta, args.out)


if __name__ == "__main__":
    main()
