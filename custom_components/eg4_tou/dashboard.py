"""The ready-made dashboard: created from the setup wizard, removed with the integration.

Home Assistant has no public API for an integration to create a dashboard, so this reaches the
storage collection Lovelace keeps its dashboard list in (found through the websocket handler it
registers, which works on every Home Assistant version we support). If that is not possible the
caller shows the manual instructions instead, so a Home Assistant change can only cost the
convenience, never the setup.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
from homeassistant.util.yaml import load_yaml_dict

_LOGGER = logging.getLogger(__name__)

URL_PATH = "eg4-tou"
TITLE = "EG4 TOU"
ICON = "mdi:battery-clock"
DASHBOARD_FILE = Path(__file__).parent / "dashboard.yaml"

CREATED = "created"
EXISTS = "exists"
UNAVAILABLE = "unavailable"


_ENTITY_ID = re.compile(r"\b[a-z_]+\.eg4_tou_[a-z0-9_]+\b")


def id_map(entries: Iterable[er.RegistryEntry]) -> dict[str, str]:
    """{id the shipped dashboard uses: the id this install really has}, for entities that were renamed
    or got a suffix (a second inverter, an older install). Only differences are listed."""
    out = {}
    for e in entries:
        default = f"{e.domain}.eg4_tou_{slugify(e.original_name or '')}"
        if e.original_name and default != e.entity_id:
            out[default] = e.entity_id
    return out


def localize(node: Any, mapping: dict[str, str]) -> Any:
    """The dashboard config with every shipped entity id replaced by this install's own."""
    if isinstance(node, dict):
        return {k: localize(v, mapping) for k, v in node.items()}
    if isinstance(node, list):
        return [localize(v, mapping) for v in node]
    if isinstance(node, str) and mapping:
        return _ENTITY_ID.sub(lambda m: mapping.get(m.group(0), m.group(0)), node)
    return node


def _collection(hass: HomeAssistant) -> Any | None:
    """Lovelace's DashboardsCollection, or None if this Home Assistant does not expose it."""
    entry = (hass.data.get("websocket_api") or {}).get("lovelace/dashboards/create")
    handler = entry[0] if isinstance(entry, tuple) else entry
    while handler is not None:
        owner = getattr(handler, "__self__", None)
        coll = getattr(owner, "storage_collection", None)
        if coll is not None:
            return coll
        handler = getattr(handler, "__wrapped__", None)
    return None


def _live_dashboards(hass: HomeAssistant) -> Any | None:
    """The mapping url_path -> LovelaceConfig that serves dashboards (a dataclass in new versions,
    a dict in old ones)."""
    data = hass.data.get("lovelace")
    live = data.get("dashboards") if isinstance(data, dict) else getattr(data, "dashboards", None)
    return live if hasattr(live, "get") else None


def _find_item(collection: Any) -> dict[str, Any] | None:
    for item in collection.async_items():
        if item.get("url_path") == URL_PATH:
            return dict(item)
    return None


async def async_create_dashboard(hass: HomeAssistant, entry: ConfigEntry | None = None) -> str:
    """Create the dashboard unless something already lives at its address. Returns CREATED, EXISTS
    or UNAVAILABLE. An existing dashboard is never touched. With `entry`, the entity ids in the
    shipped dashboard are swapped for the ones this install really has."""
    collection, live = _collection(hass), _live_dashboards(hass)
    if collection is None or live is None:
        return UNAVAILABLE
    if _find_item(collection) is not None or URL_PATH in live:
        return EXISTS
    try:
        config = await hass.async_add_executor_job(load_yaml_dict, str(DASHBOARD_FILE))
        if entry is not None:
            config = localize(config, id_map(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)))
        item = await collection.async_create_item({
            "url_path": URL_PATH, "title": TITLE, "icon": ICON,
            "show_in_sidebar": True, "require_admin": False, "allow_single_word": True})
        try:
            await live[URL_PATH].async_save(config)
        except Exception:  # noqa: BLE001 - do not leave an empty dashboard behind
            await collection.async_delete_item(item["id"])
            raise
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Could not create the EG4 TOU dashboard; see the README to add it by hand", exc_info=True)
        return UNAVAILABLE
    return CREATED


async def async_remove_dashboard(hass: HomeAssistant) -> None:
    """Delete the dashboard this integration created (a no-op if it is already gone)."""
    collection = _collection(hass)
    item = _find_item(collection) if collection is not None else None
    if collection is None or item is None:
        return
    try:
        await collection.async_delete_item(item["id"])
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Could not remove the EG4 TOU dashboard", exc_info=True)
