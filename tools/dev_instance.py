#!/usr/bin/env python3
"""A throwaway Home Assistant with this working tree installed, for trying changes by hand.

  uv run python tools/dev_instance.py                 # start (or restart) it and print the address and login
  uv run python tools/dev_instance.py --host 192.168.1.200   # skip the network scan and use this gateway
  uv run python tools/dev_instance.py --refresh       # copy the working tree in again and restart (after edits)
  uv run python tools/dev_instance.py --stop          # remove the container and its files

It starts in READ-ONLY mode and never writes to the inverter unless you switch that off in the UI, so point it at
a real gateway only if you are happy with that. Login: user `dev`, password `eg4-dev`. Needs Docker. The
instance uses host networking on port 8123 (change with --port), so stop your other Home Assistant tests first.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clean_install_test as t  # noqa: E402  (same helpers: HA client, scanning, container handling)

NAME = "eg4-dev-instance"
WORK = Path("/tmp/eg4-dev-instance")
PASSWORD = "eg4-dev"


def stop() -> None:
    t.sh("docker", "rm", "-f", NAME)
    t.force_rm(WORK)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="gateway address; skips the network scan")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--ha-image", default="ghcr.io/home-assistant/home-assistant:stable")
    ap.add_argument("--refresh", action="store_true", help="re-copy the working tree and restart the running instance")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    if args.stop:
        stop()
        print("stopped and removed")
        return 0
    if args.refresh:
        dest = WORK / "config" / "custom_components" / t.DOMAIN
        if not dest.parent.exists():
            print("no running instance; start one without --refresh", file=sys.stderr)
            return 2
        t.force_rm(dest)
        t.copy_local(WORK)
        t.sh("docker", "restart", NAME)
        print(f"restarted with the current working tree: {base}")
        return 0

    stop()
    t.copy_local(WORK)
    (WORK / "config" / "configuration.yaml").write_text("default_config:\n")
    run = t.sh("docker", "run", "-d", "--name", NAME, "--network", "host", "-e", "TZ=America/New_York",
               "-v", f"{WORK / 'config'}:/config", args.ha_image)
    if run.returncode:
        print(run.stderr, file=sys.stderr)
        return 1
    ha = t.HA(base)
    if not ha.wait_up():
        print("Home Assistant did not come up", file=sys.stderr)
        return 1
    ha.onboard("dev", PASSWORD)
    ctx = t.run_journey(ha, NAME, args, WORK)
    if not ctx.get("entry_id"):
        print("setup did not finish; see above", file=sys.stderr)
        return 1
    t.wait_entities(ha)
    ver = Path(__file__).resolve().parent.parent / "custom_components" / t.DOMAIN / "manifest.json"
    print(f"\nReady: {base}   user: dev   password: {PASSWORD}")
    print("Dashboard: sidebar > EG4 TOU. Read-only mode is on. Stop with: tools/dev_instance.py --stop")
    print("Version installed:", __import__("json").loads(ver.read_text())["version"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
