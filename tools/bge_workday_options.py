#!/usr/bin/env python3
"""Make Home Assistant's Workday sensor agree with BGE Schedule RL's holiday list.

BGE lists only eight holidays as off-peak (and the Monday after a Sunday holiday); the Workday
integration uses the whole state calendar. This prints the exact `add_holidays` and `remove_holidays`
dates to put in the Workday sensor's options so it matches BGE, for a range of years, by asking the
`holidays` library what Workday would use and comparing with this integration's BGE rule.

  uv run --with holidays --with python-dateutil python tools/bge_workday_options.py 2026 2032

Weekend dates are left out (weekends are excluded anyway). Re-run before the last year runs out, and
after a Home Assistant update that bumps the holidays library, since new or renamed holidays can appear.
Apply the result in Settings, Devices & services, Workday, Configure (add_holidays / remove_holidays).
Roll back by emptying both lists.
"""
import importlib.util
import json
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import holidays

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "eg4_tou"


def bge_rule():
    """Load planner.py without importing the Home Assistant parts of the package."""
    pkg = types.ModuleType("eg4_pure")
    pkg.__path__ = [str(PKG)]
    sys.modules["eg4_pure"] = pkg
    spec = importlib.util.spec_from_file_location("eg4_pure.planner", PKG / "planner.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eg4_pure.planner"] = mod
    spec.loader.exec_module(mod)
    return mod._bge_holiday


def lists(first: int, last: int, country="US", province="MD", language="en_US"):
    rule, add, remove = bge_rule(), [], []
    for year in range(first, last + 1):
        lib = holidays.country_holidays(country, subdiv=province, years=year, language=language)
        bge, d = {}, date(year, 1, 1)
        while d.year == year:
            if rule(d):
                bge[d] = rule(d)
            d += timedelta(days=1)
        remove += [d.isoformat() for d in sorted(lib) if d not in bge and d.weekday() < 5]
        add += [d.isoformat() for d in sorted(bge) if d not in lib and d.weekday() < 5]
    return {"add_holidays": add, "remove_holidays": remove}


if __name__ == "__main__":
    a, b = (int(x) for x in sys.argv[1:3]) if len(sys.argv) >= 3 else (date.today().year, date.today().year + 6)
    print(json.dumps(lists(a, b), indent=2))
