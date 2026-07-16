"""Bing "Photo of the Day" wallpaper source for Ted's Cards Backend.

Downloads Bing's daily images into an isolated ``backgrounds/bing_pod/`` cache
(kept separate from the bundled Built-in wallpapers) and serves them from the
existing static path, so the frontend can analyse their luminance (Mood
matching / Readability scrim) without cross-origin canvas tainting.

The cache accumulates over time up to a configurable cap
(``background_bing_cache_size``, default 100); the oldest images are pruned
beyond it. A small ``index.json`` sidecar persists each day's title/copyright so
attribution survives restarts even for days no longer in Bing's 8-day archive.
"""

from __future__ import annotations

import json
import logging
import os

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_BING_HOST = "https://www.bing.com"
_BING_ARCHIVE = "/HPImageArchive.aspx"
_CACHE_DIRNAME = "bing_pod"
_INDEX_NAME = "index.json"
_URL_BASE = f"/teds_cards_backend/backgrounds/{_CACHE_DIRNAME}"
_DEFAULT_CACHE_SIZE = 100
_FETCH_DAYS = 8  # Bing's archive exposes up to the last 8 days.
_RESOLUTIONS = ("_UHD.jpg", "_1920x1080.jpg")  # try UHD first, then 1080p.


def _cache_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "backgrounds", _CACHE_DIRNAME)


def _index_path() -> str:
    return os.path.join(_cache_dir(), _INDEX_NAME)


def _bing_mkt(hass: HomeAssistant) -> str:
    """Derive Bing's market (mkt) code from HA's configured locale."""
    lang = (getattr(hass.config, "language", None) or "en").split("-")[0]
    country = getattr(hass.config, "country", None)
    if country:
        return f"{lang}-{country}"
    return "en-US"


def _cache_size(hass: HomeAssistant) -> int:
    """The effective (global) cache cap, clamped to at least 1."""
    mgr = next(iter((hass.data.get(DOMAIN) or {}).values()), None)
    if mgr is None:
        return _DEFAULT_CACHE_SIZE
    try:
        val = int(mgr.effective_settings().get("background_bing_cache_size", _DEFAULT_CACHE_SIZE))
    except (TypeError, ValueError):
        return _DEFAULT_CACHE_SIZE
    return max(1, val)


# ── blocking file helpers (run in the executor) ───────────────────────────────
def _ensure_dir() -> None:
    os.makedirs(_cache_dir(), exist_ok=True)


def _write_file(dest: str, content: bytes) -> None:
    with open(dest, "wb") as fh:
        fh.write(content)


def _load_index() -> dict:
    try:
        with open(_index_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(index: dict) -> None:
    try:
        with open(_index_path(), "w", encoding="utf-8") as fh:
            json.dump(index, fh)
    except OSError:
        pass


def cache_has_images() -> bool:
    """True when the Bing cache already holds at least one image (blocking)."""
    try:
        return any(n.lower().endswith(".jpg") for n in os.listdir(_cache_dir()))
    except OSError:
        return False


def _reconcile_and_prune(index: dict, cap: int) -> list[dict]:
    """Reconcile the metadata index with the files on disk, prune to ``cap``,
    and return the kept entries newest-first (blocking)."""
    directory = _cache_dir()
    try:
        on_disk = {
            os.path.splitext(name)[0]
            for name in os.listdir(directory)
            if name.lower().endswith(".jpg")
        }
    except OSError:
        on_disk = set()

    # Drop index entries whose file is gone; add bare entries for orphan files.
    index = {k: v for k, v in index.items() if k in on_disk}
    for startdate in on_disk:
        if startdate not in index:
            index[startdate] = {
                "url": f"{_URL_BASE}/{startdate}.jpg",
                "title": "",
                "copyright": "",
                "startdate": startdate,
            }

    # Newest first (YYYYMMDD sorts lexicographically).
    ordered = sorted(index.values(), key=lambda e: e["startdate"], reverse=True)
    cap = max(1, cap)
    keep, prune = ordered[:cap], ordered[cap:]
    for entry in prune:
        try:
            os.remove(os.path.join(directory, f"{entry['startdate']}.jpg"))
        except OSError:
            pass
    _save_index({e["startdate"]: e for e in keep})
    return keep


def _clear_cache_files() -> None:
    directory = _cache_dir()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass


# ── network ───────────────────────────────────────────────────────────────────
async def _download_image(
    session: aiohttp.ClientSession, urlbase: str, dest: str, hass: HomeAssistant
) -> bool:
    """Download a day's image, trying UHD then 1080p. Returns True on success."""
    for suffix in _RESOLUTIONS:
        url = f"{_BING_HOST}{urlbase}{suffix}"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    continue
                content = await resp.read()
        except Exception as err:  # noqa: BLE001 - best-effort per resolution
            _LOGGER.debug("Bing PoD download failed for %s (%s)", url, err)
            continue
        try:
            await hass.async_add_executor_job(_write_file, dest, content)
            return True
        except OSError as err:
            _LOGGER.debug("Bing PoD write failed for %s (%s)", dest, err)
            return False
    return False


async def fetch_and_cache_bing(hass: HomeAssistant) -> list[dict]:
    """Ensure recent Bing images are cached, prune to the cap, and return their
    metadata newest-first as ``[{url, title, copyright, startdate}, ...]``.

    Best-effort: on any network error, returns whatever is already cached.
    """
    session = async_get_clientsession(hass)
    mkt = _bing_mkt(hass)
    index = await hass.async_add_executor_job(_load_index)

    try:
        params = {"format": "js", "idx": "0", "n": str(_FETCH_DAYS), "mkt": mkt}
        async with session.get(
            f"{_BING_HOST}{_BING_ARCHIVE}",
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        images = data.get("images") or []
    except Exception as err:  # noqa: BLE001 - offline / Bing hiccup: use cache
        _LOGGER.debug("Bing PoD archive fetch failed (%s); using cached images", err)
        images = []

    await hass.async_add_executor_job(_ensure_dir)

    for img in images:
        startdate = str(img.get("startdate") or "").strip()
        urlbase = str(img.get("urlbase") or "").strip()
        if not startdate or not urlbase:
            continue
        filename = f"{startdate}.jpg"
        dest = os.path.join(_cache_dir(), filename)
        if not await hass.async_add_executor_job(os.path.exists, dest):
            if not await _download_image(session, urlbase, dest, hass):
                continue
        index[startdate] = {
            "url": f"{_URL_BASE}/{filename}",
            "title": str(img.get("title") or "").strip(),
            "copyright": str(img.get("copyright") or "").strip(),
            "startdate": startdate,
        }

    return await hass.async_add_executor_job(_reconcile_and_prune, index, _cache_size(hass))


async def clear_bing_cache(hass: HomeAssistant) -> None:
    """Delete every cached Bing image and the metadata sidecar."""
    await hass.async_add_executor_job(_clear_cache_files)
