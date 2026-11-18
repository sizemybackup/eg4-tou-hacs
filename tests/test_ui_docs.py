"""The UI-docs checker (tools/check_ui_docs.py) decides when blog articles may be stale."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "check_ui_docs.py"


@pytest.fixture
def ud(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_ui_docs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "docs").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "form.py").write_text("v1\n")
    (tmp_path / "src" / "cards").mkdir()
    (tmp_path / "src" / "cards" / "a.js").write_text("a1\n")
    (tmp_path / "src" / "other.py").write_text("not ui\n")
    (tmp_path / "docs" / "ui-docs-map.json").write_text(json.dumps({"groups": {
        "wizard": {"why": "the form", "ui_files": ["src/form.py"], "articles": ["wiz.html"], "screenshots": ["s1.webp"]},
        "cards": {"why": "the cards", "ui_files": ["src/cards/*.js"], "articles": ["dash.html"], "screenshots": []},
    }}))
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "MAP", tmp_path / "docs" / "ui-docs-map.json")
    monkeypatch.setattr(mod, "STATE", tmp_path / "docs" / "ui-docs-state.json")
    monkeypatch.setattr(mod, "CACHE", tmp_path / "cache.json")
    monkeypatch.setenv("EG4_UI_DOCS_LOCAL", str(tmp_path / "nolocal.json"))
    return mod


def hook(ud, monkeypatch, capsys, payload):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)))
    assert ud.cmd_hook() == 0                       # a hook must never fail
    return capsys.readouterr().out


def test_files_map_to_groups_including_globs(ud):
    assert ud.group_of("src/form.py") == ["wizard"]
    assert ud.group_of("src/cards/a.js") == ["cards"]
    assert ud.group_of("src/other.py") == [] and ud.group_of("README.md") == []


def test_unverified_then_ok_then_stale(ud):
    assert ud.status("wizard", ud.groups()["wizard"])[0] == "UNVERIFIED"
    assert ud.cmd_ack(["wizard"]) == 0
    assert ud.status("wizard", ud.groups()["wizard"])[0] == "OK"
    (ud.REPO / "src" / "form.py").write_text("v2\n")
    assert ud.status("wizard", ud.groups()["wizard"])[0] == "STALE"
    assert ud.status("cards", ud.groups()["cards"])[0] == "UNVERIFIED"      # untouched group unaffected


def test_a_new_file_matching_a_glob_makes_the_group_stale(ud):
    ud.cmd_ack([])
    assert ud.status("cards", ud.groups()["cards"])[0] == "OK"
    (ud.REPO / "src" / "cards" / "b.js").write_text("new card\n")
    assert ud.status("cards", ud.groups()["cards"])[0] == "STALE"


def test_ack_all_and_unknown_group(ud, capsys):
    assert ud.cmd_ack([]) == 0
    assert {n for n, g in ud.groups().items() if ud.status(n, g)[0] == "OK"} == {"wizard", "cards"}
    assert ud.cmd_ack(["nope"]) == 2


def test_check_exit_codes_and_strict(ud, capsys):
    assert ud.cmd_check(None, strict=False) == 0 and ud.cmd_check(None, strict=True) == 1
    ud.cmd_ack([])
    assert ud.cmd_check(None, strict=True) == 0
    assert "wiz.html" not in capsys.readouterr().out.split("[OK]")[-1]


def test_hook_speaks_for_ui_files_only(ud, monkeypatch, capsys):
    p = lambda f: {"tool_name": "Edit", "tool_input": {"file_path": str(ud.REPO / f)}}
    out = json.loads(hook(ud, monkeypatch, capsys, p("src/form.py")))["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUse"
    assert "wiz.html" in out["additionalContext"] and "s1.webp" in out["additionalContext"] and "ack wizard" in out["additionalContext"]
    assert hook(ud, monkeypatch, capsys, p("src/other.py")) == ""                       # not a UI file
    assert hook(ud, monkeypatch, capsys, {"tool_input": {"file_path": "/etc/hosts"}}) == ""   # outside the repo
    assert hook(ud, monkeypatch, capsys, "not json") == "" and hook(ud, monkeypatch, capsys, {}) == ""
    # MultiEdit/Write payloads that only carry tool_response.filePath still work
    out2 = hook(ud, monkeypatch, capsys, {"tool_response": {"filePath": str(ud.REPO / "src/cards/a.js")}})
    assert "dash.html" in json.loads(out2)["hookSpecificOutput"]["additionalContext"]


def test_hook_is_short_on_repeat_and_silent_once_verified(ud, monkeypatch, capsys):
    payload = {"tool_input": {"file_path": str(ud.REPO / "src/form.py")}}
    first = json.loads(hook(ud, monkeypatch, capsys, payload))["hookSpecificOutput"]["additionalContext"]
    assert "wiz.html" in first
    # the file is edited again, so the content (and hash) differs: still stale, shorter message
    (ud.REPO / "src" / "form.py").write_text("v3\n")
    again = json.loads(hook(ud, monkeypatch, capsys, payload))["hookSpecificOutput"]["additionalContext"]
    assert "wiz.html" in again                                     # new content: full notice again
    same = json.loads(hook(ud, monkeypatch, capsys, payload))["hookSpecificOutput"]["additionalContext"]
    assert "Still unverified" in same and "wiz.html" not in same   # nothing new since the last notice
    ud.cmd_ack(["wizard"])
    capsys.readouterr()                                            # drop the ack command's own message
    assert hook(ud, monkeypatch, capsys, payload) == ""            # verified: stop nagging


def test_pre_commit_warns_for_staged_ui_files_and_strict_blocks(ud, monkeypatch, capsys):
    run = lambda *a: subprocess.run(["git", "-C", str(ud.REPO), *a], check=True, capture_output=True)
    run("init", "-q"); run("config", "user.email", "t@example.com"); run("config", "user.name", "t")
    run("add", "-A")
    assert ud.cmd_git(strict=False) == 0                                    # warning only
    err = capsys.readouterr().err
    assert "wizard" in err and "wiz.html" in err and "ack" in err
    assert ud.cmd_git(strict=True) == 1                                     # strict mode blocks
    ud.cmd_ack([])
    capsys.readouterr()
    assert ud.cmd_git(strict=True) == 0 and capsys.readouterr().err == ""   # verified: quiet
    (ud.REPO / "src" / "other.py").write_text("changed, not UI\n")
    run("add", "-A")
    assert ud.cmd_git(strict=True) == 0                                     # non-UI change: quiet


def test_the_real_map_is_consistent():
    """Every UI file pattern in the committed map matches something, and group names are unique."""
    real = json.loads((SCRIPT.parent.parent / "docs" / "ui-docs-map.json").read_text())["groups"]
    spec = importlib.util.spec_from_file_location("check_ui_docs_real", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name, g in real.items():
        assert mod.tracked_files(g["ui_files"]), f"group {name} matches no files"
        assert g["articles"] or g["screenshots"], name


def test_verified_state_file_is_tracked_not_swallowed_by_the_secret_ignore_rules():
    root = SCRIPT.parent.parent
    r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "docs/ui-docs-verified.json"])
    assert r.returncode == 1, "docs/ui-docs-verified.json is gitignored: a fresh clone would see every group unverified"
