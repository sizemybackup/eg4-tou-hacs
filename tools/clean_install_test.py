#!/usr/bin/env python3
"""Clean-install test: does a PUBLISHED release install and work in a brand-new Home Assistant?

It reproduces what HACS / the Marketplace do (download the release from GitHub, put
custom_components/eg4_tou in a fresh config folder), then walks the new-user journey in a throwaway
Docker container and prints a PASS/FAIL table. Nothing from your own working tree is used.

  uv run --with playwright python tools/clean_install_test.py                      # latest release, HA stable
  uv run --with playwright python tools/clean_install_test.py --ref v0.10.0 \
        --ha-image ghcr.io/home-assistant/home-assistant:2025.1.4                  # oldest supported HA
  uv run --with playwright python tools/clean_install_test.py --local                   # the working tree, before tagging
  python3 tools/clean_install_test.py --no-browser                                 # skip the dashboard checks

Needs Docker. The container uses host networking so the setup wizard's network scan can find a real
gateway on your LAN (read-only reads only; read-only mode is chosen in the wizard, nothing is written).
Give --host to skip the scan. Without a reachable gateway the journey steps fail with a clear message.

What is checked: archive layout and manifest, container start with no errors from the integration,
setup wizard (scan, tariff, read-only, dashboard), the dashboard it creates (and its removal), entities and their data, card files served and registered,
Configure (options flow) and the reload it triggers, dashboard cards render with no JavaScript errors,
the write switches are configuration entities, survives a Home Assistant restart, and uninstalls
cleanly (entry removed, folder deleted, restart without errors).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO = "sizemybackup/eg4-tou-hacs"
DOMAIN = "eg4_tou"
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool | None, detail: str = "") -> bool:
    status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f": {detail}" if detail else ""), flush=True)
    return bool(ok)


def sh(*args: str, check=False, timeout=300) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=check)


def force_rm(path: Path) -> None:
    """Delete a folder the container may have filled with root-owned files (e.g. __pycache__)."""
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        sh("docker", "run", "--rm", "-v", f"{path.parent}:/x", "alpine", "rm", "-rf", f"/x/{path.name}")


class HA:
    def __init__(self, base: str):
        self.base, self.access, self.refresh, self.expires = base, None, None, 0

    def call(self, method: str, path: str, body=None, form=False, auth=True, timeout=60):
        data = None
        headers = {}
        if body is not None:
            data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        if auth:
            headers["Authorization"] = "Bearer " + self.token()
        req = urllib.request.Request(self.base + path, data, headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode(errors="replace"))
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode(errors="replace"))
        except (urllib.error.URLError, OSError, ConnectionError) as e:
            return 0, str(e)

    def token(self) -> str:
        if self.access and time.time() < self.expires - 120:
            return self.access
        s, t = self.call("POST", "/auth/token", {"grant_type": "refresh_token", "refresh_token": self.refresh,
                                                 "client_id": self.base + "/"}, form=True, auth=False)
        self.access, self.expires = t["access_token"], time.time() + t["expires_in"]
        return self.access

    def wait_up(self, timeout=240) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.call("GET", "/api/onboarding", auth=False, timeout=5)[0] == 200 or \
               self.call("GET", "/manifest.json", auth=False, timeout=5)[0] == 200:
                return True
            time.sleep(3)
        return False

    def onboard(self) -> None:
        cid = self.base + "/"
        _, u = self.call("POST", "/api/onboarding/users", {"client_id": cid, "name": "Test", "username": "test",
                                                           "password": "test-" + str(int(time.time())), "language": "en"}, auth=False)
        _, t = self.call("POST", "/auth/token", {"grant_type": "authorization_code", "code": u["auth_code"], "client_id": cid},
                         form=True, auth=False)
        self.access, self.refresh, self.expires = t["access_token"], t["refresh_token"], time.time() + t["expires_in"]
        for step in ("core_config", "analytics"):
            self.call("POST", f"/api/onboarding/{step}", {})
        self.call("POST", "/api/onboarding/integration", {"client_id": cid, "redirect_uri": cid + "?auth_callback=1"})

    def states(self) -> dict:
        s, d = self.call("GET", "/api/states")
        return {x["entity_id"]: x for x in d} if s == 200 else {}


def our_log_problems(logs: str) -> list[str]:
    """Warnings/errors that mention this integration, minus the standard 'custom integration' notice."""
    clean = re.sub(r"\x1b\[[0-9;]*m", "", logs)
    out = []
    lines = clean.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"\b(ERROR|WARNING|CRITICAL)\b", line) and (DOMAIN in line or "eg4_tou" in line):
            if "has not been tested by Home Assistant" in line:
                continue
            out.append(line.strip()[:240])
        elif "Traceback" in line and any(DOMAIN in l for l in lines[i:i + 12]):
            out.append("Traceback mentioning " + DOMAIN)
    return out


def copy_local(work: Path) -> Path | None:
    """--local: the working tree's integration folder, for a pre-release check. Same layout as a download."""
    src = Path(__file__).resolve().parent.parent / "custom_components" / DOMAIN
    dest = work / "config" / "custom_components" / DOMAIN
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    n = sum(1 for p in dest.rglob("*") if p.is_file())
    record("copy the working tree's integration folder (--local)", n > 0, f"{n} files")
    return dest


def download_release(ref: str, work: Path) -> Path | None:
    url = f"https://github.com/{REPO}/archive/refs/tags/{ref}.zip"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            blob = r.read()
    except Exception as e:  # noqa: BLE001
        record("download the release archive from GitHub", False, f"{url}: {e}")
        return None
    z = zipfile.ZipFile(io.BytesIO(blob))
    root = z.namelist()[0].split("/")[0]
    dest = work / "config" / "custom_components"
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in z.namelist():
        if name.startswith(f"{root}/custom_components/{DOMAIN}/") and not name.endswith("/"):
            target = dest / name[len(f"{root}/custom_components/"):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
            n += 1
    record("download the release archive from GitHub", n > 0, f"{ref}: {n} files, {len(blob) // 1024} KB")
    return dest / DOMAIN if n else None


def check_layout(pkg: Path, ref: str) -> None:
    ref = ref if ref != "local" else "v" + json.loads((pkg / "manifest.json").read_text())["version"]
    man = json.loads((pkg / "manifest.json").read_text())
    need = {"domain", "name", "version", "documentation", "issue_tracker", "codeowners", "requirements", "config_flow"}
    record("manifest has the keys HACS/hassfest require", need <= set(man), "missing: " + ", ".join(sorted(need - set(man))) if need - set(man) else f"v{man['version']}")
    record("manifest version matches the release tag", man["version"] == ref.lstrip("v"), f"manifest {man['version']} vs tag {ref}")
    files = ["__init__.py", "config_flow.py", "translations/en.json", "frontend/eg4-tou-timeline-card.js",
             "frontend/eg4-tou-rules-card.js", "dashboard.yaml", "brand/icon.png", "brand/dark_icon.png", "brand/logo.png"]
    miss = [f for f in files if not (pkg / f).exists()]
    record("all runtime files are present in the archive", not miss, ", ".join(miss) or f"{sum(1 for _ in pkg.rglob('*') if _.is_file())} files")
    record("integration does not ship tests or secrets", not any(p.suffix in (".env", ".pem", ".key") or p.name.startswith("test_") for p in pkg.rglob("*")))


def run_journey(ha: HA, container: str, args, work: Path) -> dict:
    ctx: dict = {}
    # --- start the setup wizard
    s, flow = ha.call("POST", "/api/config/config_entries/flow", {"handler": DOMAIN, "show_advanced_options": False})
    if not record("the integration is found and its setup wizard opens", s == 200 and flow.get("step_id") == "user", f"http {s} {str(flow)[:160]}"):
        return ctx
    note = (flow.get("description_placeholders") or {}).get("scan_result", "")
    host = args.host
    if not host:
        for f in flow.get("data_schema", []):
            if f.get("name") == "host":
                host = f.get("default") or ""
        record("the network scan runs and reports a result", bool(note), note.splitlines()[0][:90] if note else "no scan text")
        if not host:
            record("a gateway was detected on the LAN", False, "none found; pass --host <gateway ip> to test without the scan")
            return ctx
    s, nxt = ha.call("POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                     {"host": host, "port": 502, "unit": 1, "country": "US", "subdiv": "MD"}, timeout=120)
    if not record("the gateway is accepted (read-only probe)", s == 200 and nxt.get("step_id") == "tariff", f"{host}: {str(nxt)[:160]}"):
        return ctx
    s, nxt = ha.call("POST", f"/api/config/config_entries/flow/{nxt['flow_id']}",
                     {"tariff": "bge_rl", "offpeak_weekends": True, "offpeak_holidays": True})
    record("tariff step accepts the BGE preset", s == 200 and nxt.get("step_id") == "mode", str(nxt)[:120])
    s, nxt = ha.call("POST", f"/api/config/config_entries/flow/{nxt['flow_id']}", {"mode": "read_only"})
    record("the wizard offers the dashboard as its last step", s == 200 and nxt.get("step_id") == "dashboard", str(nxt)[:120])
    s, done = ha.call("POST", f"/api/config/config_entries/flow/{nxt['flow_id']}", {"create_dashboard": True})
    if not record("setup finishes in read-only mode and creates the entry", s == 200 and done.get("type") == "create_entry",
                  str(done)[:200]):
        return ctx
    ctx["entry_id"] = done["result"]["entry_id"] if isinstance(done.get("result"), dict) else done.get("result")
    ctx["host"] = host
    return ctx


def wait_entities(ha: HA, timeout=240) -> dict:
    end, st = time.time() + timeout, {}
    while time.time() < end:
        st = {k: v for k, v in ha.states().items() if k.split(".", 1)[1].startswith("eg4_tou_")}
        prof = st.get("sensor.eg4_tou_active_profile")
        if len(st) >= 11 and prof and prof["state"] not in ("unavailable", "unknown"):
            return st
        time.sleep(5)
    return st


def check_entities(st: dict) -> None:
    want = ["sensor.eg4_tou_active_profile", "sensor.eg4_tou_registers_out_of_sync", "sensor.eg4_tou_controller_status",
            "switch.eg4_tou_controller_enabled", "switch.eg4_tou_read_only_mode", "number.eg4_tou_ac_charge_power",
            "number.eg4_tou_charge_limit_summer_weekday", "number.eg4_tou_charge_limit_winter_weekday",
            "number.eg4_tou_charge_limit_weekend_holiday", "time.eg4_tou_charge_start", "time.eg4_tou_charge_end"]
    miss = [e for e in want if e not in st]
    record("all 11 entities exist with predictable ids", not miss, ", ".join(miss) or "ok")
    bad = [e for e in want if e in st and st[e]["state"] in ("unavailable",)]
    record("no entity is unavailable", not bad, ", ".join(bad))
    prof = st.get("sensor.eg4_tou_active_profile", {})
    a = prof.get("attributes", {})
    record("active profile has plan, timeline, week and facts", all(k in a for k in ("rules", "timeline", "week", "facts")) and len(a.get("week", [])) == 8,
           f"profile={prof.get('state')}")
    record("controller is in read-only mode", st.get("sensor.eg4_tou_controller_status", {}).get("state") == "read_only" and
           st.get("switch.eg4_tou_read_only_mode", {}).get("state") == "on", st.get("sensor.eg4_tou_controller_status", {}).get("state", "?"))


def check_frontend(ha: HA, version: str) -> None:
    for name, tag in (("eg4-tou-timeline-card.js", "eg4-tou-timeline-card"), ("eg4-tou-rules-card.js", "eg4-tou-rules-card")):
        s, body = ha.call("GET", f"/{DOMAIN}/{name}", auth=False)
        record(f"card file {name} is served", s == 200 and tag in str(body), f"http {s}")
    s, html = ha.call("GET", "/", auth=False)
    record("both cards are registered with the frontend", all(f"/{DOMAIN}/{n}?v={version}" in str(html) for n in
           ("eg4-tou-timeline-card.js", "eg4-tou-rules-card.js")), f"?v={version}")


def browser_checks(ha: HA, work: Path, base: str) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        record("dashboard cards render in a real browser", None, "playwright not installed (use: uv run --with playwright ...)")
        return
    chrome = Path.home() / ".cache" / "ms-playwright" / "chromium-1140" / "chrome-linux" / "chrome"
    tokens = {"access_token": ha.token(), "token_type": "Bearer", "expires_in": 1800, "hassUrl": base, "clientId": base + "/",
              "expires": int((time.time() + 1800) * 1000), "refresh_token": ha.refresh}
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": str(chrome)} if chrome.exists() else {}), args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1500, "height": 1100}, color_scheme="dark")
        ctx.add_init_script("localStorage.setItem('hassTokens', %s);" % json.dumps(json.dumps(tokens)))
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append("pageerror: " + str(e)[:200]))
        pg.on("console", lambda m: errors.append("console: " + m.text[:200]) if m.type == "error" else None)
        pg.goto(base + "/config/dashboard"); pg.wait_for_timeout(6000)
        dashes = pg.evaluate("""async () => { const h = document.querySelector('home-assistant').hass;
            const l = await h.callWS({type: 'lovelace/dashboards/list'}); return l.map(d => [d.url_path, d.title, d.show_in_sidebar]); }""")
        record("the wizard created the EG4 TOU dashboard in the sidebar", ["eg4-tou", "EG4 TOU", True] in dashes, str(dashes))
        cfg = pg.evaluate("""async () => { const h = document.querySelector('home-assistant').hass;
            return await h.callWS({type: 'lovelace/config', url_path: 'eg4-tou'}); }""")
        record("the dashboard holds the shipped views and both cards", cfg.get("views", [{}])[0].get("path") == "schedule"
               and "eg4-tou-rules-card" in json.dumps(cfg) and "eg4-tou-timeline-card" in json.dumps(cfg), str(list(cfg))[:80])
        pg.goto(base + "/eg4-tou/schedule"); pg.wait_for_timeout(9000)
        defined = pg.evaluate("() => [!!customElements.get('eg4-tou-rules-card'), !!customElements.get('eg4-tou-timeline-card')]")
        record("both custom cards are defined in the browser", all(defined), str(defined))
        text = pg.evaluate("""() => { const out = {}; const walk = (r) => { for (const e of r.querySelectorAll('*')) {
            if (e.tagName.toLowerCase() === 'eg4-tou-rules-card') out.rules = e.shadowRoot ? e.shadowRoot.textContent : '';
            if (e.tagName.toLowerCase() === 'eg4-tou-timeline-card') out.timeline = e.shadowRoot ? e.shadowRoot.textContent : '';
            if (e.shadowRoot) walk(e.shadowRoot); } }; walk(document); return out; }""")
        record("the rules card shows live data", "Season" in text.get("rules", "") and "Next" in text.get("rules", ""), text.get("rules", "")[:70].replace("\n", " "))
        record("the timeline card shows live data", "On-peak" in text.get("timeline", ""), text.get("timeline", "")[:70].replace("\n", " "))
        reg = pg.evaluate("""async () => { const h = document.querySelector('home-assistant').hass;
            const out = {}; for (const id of ['switch.eg4_tou_read_only_mode', 'switch.eg4_tou_controller_enabled'])
              { const e = await h.callWS({type: 'config/entity_registry/get', entity_id: id}); out[id] = e.entity_category; } return out; }""")
        record("the two write switches are configuration entities", all(v == "config" for v in reg.values()), str(reg))
        pg.screenshot(path=str(work / "dashboard.png"))
        b.close()
    ours = [e for e in errors if re.search(r"eg4|custom element|eg4-tou", e, re.I)]
    record("no JavaScript errors from the cards", not ours, "; ".join(ours)[:200] or f"{len(errors)} unrelated browser message(s)")


def dashboard_paths(ha: HA, base: str) -> list[str] | None:
    """url_paths of the storage dashboards, read through the frontend's websocket (None: no browser)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    chrome = Path.home() / ".cache" / "ms-playwright" / "chromium-1140" / "chrome-linux" / "chrome"
    tokens = {"access_token": ha.token(), "token_type": "Bearer", "expires_in": 1800, "hassUrl": base, "clientId": base + "/",
              "expires": int((time.time() + 1800) * 1000), "refresh_token": ha.refresh}
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": str(chrome)} if chrome.exists() else {}), args=["--no-sandbox"])
        ctx = b.new_context()
        ctx.add_init_script("localStorage.setItem('hassTokens', %s);" % json.dumps(json.dumps(tokens)))
        pg = ctx.new_page()
        pg.goto(base + "/config/dashboard"); pg.wait_for_timeout(6000)
        out = pg.evaluate("""async () => { const h = document.querySelector('home-assistant').hass;
            return (await h.callWS({type: 'lovelace/dashboards/list'})).map(d => d.url_path); }""")
        b.close()
    return out


def options_and_reload(ha: HA, entry_id: str) -> None:
    s, f = ha.call("POST", "/api/config/config_entries/options/flow", {"handler": entry_id})
    if not record("Configure (options flow) opens the tariff screen", s == 200 and f.get("step_id") == "tariff", f"http {s} {str(f)[:140]}"):
        return
    s, f2 = ha.call("POST", f"/api/config/config_entries/options/flow/{f['flow_id']}",
                    {"tariff": "bge_rl", "offpeak_weekends": True, "offpeak_holidays": True})
    record("Configure saves a changed tariff", s == 200 and f2.get("type") == "create_entry", f"http {s} {str(f2)[:140]}")
    time.sleep(8)                                    # the entry reloads itself
    st = wait_entities(ha, 150)
    record("the entry reloads after Configure and the entities come back", len(st) >= 11 and
           st.get("sensor.eg4_tou_active_profile", {}).get("state") not in (None, "unavailable"), f"{len(st)} entities")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", help="release tag to test (default: the latest release)")
    ap.add_argument("--local", action="store_true", help="test the working tree instead of a published release (pre-release check)")
    ap.add_argument("--ha-image", default="ghcr.io/home-assistant/home-assistant:stable")
    ap.add_argument("--host", help="gateway address; skips the network scan")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--keep", action="store_true", help="leave the container and files for inspection")
    args = ap.parse_args()

    if shutil.which("docker") is None:
        print("docker is required", file=sys.stderr)
        return 2
    ref = "local" if args.local else args.ref
    if not ref:
        with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=30) as r:
            ref = json.load(r)["tag_name"]
    print(f"== clean install test: {REPO} {ref} on {args.ha_image}", flush=True)
    work = Path(tempfile.mkdtemp(prefix="eg4-clean-install-"))
    container = "eg4-clean-install-test"
    sh("docker", "rm", "-f", container)
    pkg = copy_local(work) if args.local else download_release(ref, work)
    if pkg:
        check_layout(pkg, ref)
        (work / "config" / "configuration.yaml").write_text("default_config:\n")   # what a fresh user has
        run = sh("docker", "run", "-d", "--name", container, "--network", "host", "-e", "TZ=America/New_York",
                 "-v", f"{work / 'config'}:/config", args.ha_image)
        if record("a fresh Home Assistant container starts", run.returncode == 0, args.ha_image if run.returncode == 0 else run.stderr[:160]):
            base = f"http://127.0.0.1:{args.port}"
            ha = HA(base)
            if record("Home Assistant comes up", ha.wait_up(), base):
                ha.onboard()
                _, ver = ha.call("GET", "/api/config")
                print(f"   Home Assistant {ver.get('version') if isinstance(ver, dict) else ver}", flush=True)
                ctx = run_journey(ha, container, args, work)
                if ctx.get("entry_id"):
                    st = wait_entities(ha)
                    check_entities(st)
                    check_frontend(ha, json.loads((pkg / "manifest.json").read_text())["version"])
                    probs = our_log_problems(sh("docker", "logs", container).stdout + sh("docker", "logs", container).stderr)
                    record("no warnings or errors from the integration in the log", not probs, " | ".join(probs[:3]))
                    if not args.no_browser:
                        browser_checks(ha, work, base)
                    options_and_reload(ha, ctx["entry_id"])
                    # survives a restart
                    sh("docker", "restart", container)
                    ha.wait_up()
                    st = wait_entities(ha, 240)
                    record("after a Home Assistant restart the integration loads again", len(st) >= 11 and
                           st.get("sensor.eg4_tou_active_profile", {}).get("state") not in (None, "unavailable"), f"{len(st)} entities")
                    # uninstall
                    s, _ = ha.call("DELETE", f"/api/config/config_entries/entry/{ctx['entry_id']}")
                    time.sleep(4)
                    left = [k for k in ha.states() if k.split(".", 1)[1].startswith("eg4_tou_")]
                    record("removing the integration removes its entities", s == 200 and not left, f"{len(left)} left")
                    if not args.no_browser:
                        paths = dashboard_paths(ha, base)
                        record("removing the integration removes the dashboard it created", paths is not None and "eg4-tou" not in paths, str(paths))
                    sh("docker", "exec", container, "rm", "-rf", f"/config/custom_components/{DOMAIN}")
                    sh("docker", "restart", container)
                    ha.wait_up()
                    time.sleep(20)
                    logs = sh("docker", "logs", "--since", "60s", container)
                    record("after deleting the folder Home Assistant restarts with no errors from it",
                           not our_log_problems(logs.stdout + logs.stderr))
                    s, _ = ha.call("GET", f"/{DOMAIN}/eg4-tou-rules-card.js", auth=False)
                    record("no card files are left being served", s == 404, f"http {s}")
    final = sh("docker", "logs", container)
    (work / "ha.log").write_text(re.sub(r"\x1b\[[0-9;]*m", "", final.stdout + final.stderr))
    if not args.keep:
        sh("docker", "rm", "-f", container)
        logp = work / "ha.log"
        keep = Path(tempfile.gettempdir()) / f"eg4-clean-install-{ref}.log"
        shutil.copy(logp, keep)
        force_rm(work)
        print(f"   Home Assistant log kept at {keep}")
    else:
        print(f"   kept: container {container}, files {work}")
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    print(f"\n== {len(RESULTS) - len(fails)} passed, {len(fails)} failed, {sum(1 for r in RESULTS if r[0] == 'SKIP')} skipped")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
