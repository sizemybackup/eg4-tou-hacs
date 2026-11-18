#!/usr/bin/env python3
"""Do the blog articles and screenshots still match the UI? (stdlib only)

The map (docs/ui-docs-map.json) says which source files change what users see and which
articles/screenshots show it. The state (docs/ui-docs-verified.json) records a hash of each group's
files at the moment someone confirmed the articles match. Any difference means "stale".

  check_ui_docs.py                 report every group (exit 0; --strict exits 1 if any is stale)
  check_ui_docs.py --hook          Claude Code PostToolUse hook: reads the hook JSON on stdin, and when
                                   the edited file is a UI file prints JSON context for Claude
  check_ui_docs.py --git           pre-commit: report only groups touched by the staged files
  check_ui_docs.py ack [group ...] record the current hashes (after the articles/screenshots are updated);
                                   no group = all groups
  check_ui_docs.py --list          show the map

Local paths for the articles and screenshots come from ~/.config/eg4-ui-docs.json (or $EG4_UI_DOCS_LOCAL):
  {"articles_dir": "...", "images_dir": "..."}   # kept out of the public repo on purpose
Set UI_DOCS_STRICT=1 to make --git fail (block the commit) instead of warning.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "docs" / "ui-docs-map.json"
STATE = REPO / "docs" / "ui-docs-verified.json"
CACHE = Path(os.environ.get("EG4_UI_DOCS_CACHE", Path.home() / ".cache" / "eg4-ui-docs-notified.json"))
SELF = "python3 " + str(Path(__file__).resolve())


def load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def groups() -> dict:
    return load(MAP, {}).get("groups", {})


def tracked_files(patterns: list[str]) -> list[str]:
    out = []
    for pat in patterns:
        if any(c in pat for c in "*?["):
            base = pat.split("*")[0].rsplit("/", 1)[0] if "/" in pat.split("*")[0] else "."
            for p in sorted((REPO / base).rglob("*")):
                rel = p.relative_to(REPO).as_posix()
                if p.is_file() and "__pycache__" not in rel and fnmatch.fnmatch(rel, pat):
                    out.append(rel)
        elif (REPO / pat).is_file():
            out.append(pat)
    return sorted(set(out))


def group_hash(g: dict) -> str:
    h = hashlib.sha256()
    for rel in tracked_files(g["ui_files"]):
        h.update(rel.encode() + b"\0" + hashlib.sha256((REPO / rel).read_bytes()).digest())
    return h.hexdigest()


def status(name: str, g: dict) -> tuple[str, str]:
    """(STATE, hash) where STATE is OK, STALE or UNVERIFIED."""
    cur = group_hash(g)
    rec = load(STATE, {}).get("groups", {}).get(name)
    if not rec:
        return "UNVERIFIED", cur
    return ("OK" if rec.get("hash") == cur else "STALE"), cur


def group_of(rel: str) -> list[str]:
    return [n for n, g in groups().items() if any(fnmatch.fnmatch(rel, p) for p in g["ui_files"])]


def local_paths() -> dict:
    p = Path(os.environ.get("EG4_UI_DOCS_LOCAL", Path.home() / ".config" / "eg4-ui-docs.json"))
    cfg = load(p, {})
    return {k: Path(v).expanduser() for k, v in cfg.items() if isinstance(v, str)}


def hints(g: dict) -> list[str]:
    """Extra, cheap checks: missing files, and screenshots older than the UI source's last commit."""
    lp, out = local_paths(), []
    arts, imgs = lp.get("articles_dir"), lp.get("images_dir")
    try:
        ts = int(subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%ct", "--", *tracked_files(g["ui_files"])],
                                capture_output=True, text=True, timeout=10).stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        ts = 0
    for a in g["articles"]:
        if arts and not (arts / a).exists():
            out.append(f"article missing locally: {a}")
    for s in g["screenshots"]:
        if imgs and not (imgs / s).exists():
            out.append(f"screenshot missing locally: {s}")
        elif imgs and ts and (imgs / s).stat().st_mtime < ts:
            out.append(f"screenshot older than the last UI commit: {s}")
    return out


def describe(name: str, g: dict, state: str) -> str:
    lines = [f"[{state}] {name}: {g.get('why', '')}"]
    lines += [f"    article:    {a}" for a in g["articles"]]
    lines += [f"    screenshot: {s}" for s in g["screenshots"]]
    lines += [f"    hint:       {h}" for h in hints(g)]
    return "\n".join(lines)


def cmd_check(only: list[str] | None, strict: bool) -> int:
    bad = 0
    for name, g in groups().items():
        if only is not None and name not in only:
            continue
        st, _ = status(name, g)
        if st == "OK":
            print(f"[OK]    {name}")
        else:
            bad += 1
            print(describe(name, g, st))
    if bad:
        print(f"\n{bad} group(s) need a look: update the screenshots/articles, then run: {SELF} ack <group>")
    return 1 if (bad and strict) else 0


def cmd_ack(names: list[str]) -> int:
    gs, state = groups(), load(STATE, {"groups": {}})
    for name in names or list(gs):
        if name not in gs:
            print(f"unknown group: {name}", file=sys.stderr)
            return 2
        state.setdefault("groups", {})[name] = {"hash": group_hash(gs[name])}
        print(f"verified: {name}")
    state["_about"] = "Hashes of each UI group when the blog articles and screenshots were last confirmed to match."
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return 0


def cmd_hook() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    ti, tr = payload.get("tool_input") or {}, payload.get("tool_response") or {}
    path = ti.get("file_path") or (tr.get("filePath") if isinstance(tr, dict) else None)
    if not path:
        return 0
    try:
        rel = Path(path).resolve().relative_to(REPO).as_posix()
    except ValueError:
        return 0                                   # not a file in this repo: say nothing
    names = group_of(rel)
    if not names:
        return 0
    notified = load(CACHE, {})
    first, parts = [], []
    for n in names:
        g = groups()[n]
        st, cur = status(n, g)
        if st == "OK":                             # edited back to the verified state
            continue
        (first if notified.get(n) != cur else parts).append(n)
        notified[n] = cur
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(notified))
    except OSError:
        pass
    msg = []
    for n in first:
        g = groups()[n]
        msg.append(
            f"UI file changed: {rel} (group '{n}': {g.get('why', '')}). The blog articles and screenshots that show it may now be out of date.\n"
            + "\n".join(f"  - article: {a}" for a in g["articles"]) + "\n"
            + "\n".join(f"  - screenshot: {s}" for s in g["screenshots"])
            + f"\nDecide whether the visible UI or printed numbers changed. If so, retake the screenshots (clean demo Home Assistant, see the project memory note) and update the articles in the sizemybackup site, then record it with: {SELF} ack {n}. If the change is not visible to users, run the ack command anyway so this stops flagging.")
    if parts:
        msg.append("Still unverified after more edits: " + ", ".join(parts) + f" (run '{SELF}' to see the list).")
    if msg:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n\n".join(msg)}}))
    return 0


def cmd_git(strict: bool) -> int:
    staged = subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout.split()
    touched = sorted({n for f in staged for n in group_of(f)})
    if not touched:
        return 0
    bad = [n for n in touched if status(n, groups()[n])[0] != "OK"]
    if not bad:
        return 0
    print("\nThis commit changes UI that blog articles and screenshots show:", file=sys.stderr)
    for n in bad:
        print(describe(n, groups()[n], status(n, groups()[n])[0]), file=sys.stderr)
    print(f"\nUpdate them, then: {SELF} ack {' '.join(bad)}", file=sys.stderr)
    print("(warning only; set UI_DOCS_STRICT=1 to block commits until acknowledged)\n", file=sys.stderr)
    return 1 if strict else 0


def main(argv: list[str]) -> int:
    strict = "--strict" in argv or os.environ.get("UI_DOCS_STRICT") == "1"
    args = [a for a in argv if not a.startswith("--")]
    if "--hook" in argv:
        return cmd_hook()
    if "--git" in argv:
        return cmd_git(strict)
    if "--list" in argv:
        for n, g in groups().items():
            print(f"{n}: {', '.join(g['ui_files'])}")
        return 0
    if args[:1] == ["ack"]:
        return cmd_ack(args[1:])
    return cmd_check(args or None, strict)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
