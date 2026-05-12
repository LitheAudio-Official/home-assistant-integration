"""Prayer-time scheduler for Lithe Audio.

Service: lithe_audio.set_prayer_schedule

data:
  city:    "London"
  country: "GB"
  method:  2                  # ISNA=2, MWL=3, Egyptian=5
  entries:
    - prayer: "fajr"
      speakers: ["192.168.1.38", "192.168.1.17"]
      url: "http://server/adhan.mp3"
      volume: 70
      days: "daily"           # daily | weekdays | weekends | friday
    - time: "07:00"           # fixed-time fallback (HH:MM)
      speakers: ["media_player.deck_v3"]
      url: "http://server/morning.mp3"
      volume: 50
      days: "weekdays"

The integration fetches prayer times once and re-fetches each day at 00:01.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change

from .const import ALADHAN_URL, DATA_PRAYER, DATA_TANNOY_SAVED, DOMAIN, PRAYER_NAMES

_LOGGER = logging.getLogger(__name__)


async def async_fetch_prayer_times(
    hass: HomeAssistant, city: str, country: str, method: int = 2
) -> dict[str, str]:
    """Return dict {prayer_name: "HH:MM"} for today, in local time."""
    session = async_get_clientsession(hass)
    params = {"city": city, "country": country, "method": method}
    try:
        async with session.get(ALADHAN_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
    except Exception as e:
        _LOGGER.error("Aladhan fetch failed: %s", e)
        return {}
    if data.get("code") != 200:
        _LOGGER.error("Aladhan error: %s", data.get("status"))
        return {}
    timings = data.get("data", {}).get("timings", {})
    return {k.lower(): v[:5] for k, v in timings.items() if k.lower() in PRAYER_NAMES}


def _day_matches(days: str, dow: int) -> bool:
    """dow: 0=Monday … 6=Sunday."""
    if not days or days == "daily":
        return True
    if days == "weekdays":
        return dow < 5
    if days == "weekends":
        return dow >= 5
    if days == "friday":
        return dow == 4
    if days == "saturday":
        return dow == 5
    if days == "sunday":
        return dow == 6
    return True


class PrayerScheduler:
    """Registers HA time-change listeners for each prayer time daily."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self.hass = hass
        self.config = config
        self._unsubs: list = []
        self._midnight_unsub = None

    async def async_setup(self) -> None:
        await self._schedule_today()
        # Re-schedule every day at 00:01
        self._midnight_unsub = async_track_time_change(
            self.hass, self._midnight_refresh, hour=0, minute=1, second=0,
        )

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs.clear()
        if self._midnight_unsub:
            try:
                self._midnight_unsub()
            except Exception:
                pass
            self._midnight_unsub = None

    async def _midnight_refresh(self, _now: datetime) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs.clear()
        await self._schedule_today()

    async def _schedule_today(self) -> None:
        cfg     = self.config
        city    = cfg.get("city", "")
        country = cfg.get("country", "")
        method  = int(cfg.get("method", 2))
        entries = cfg.get("entries", []) or []

        times: dict[str, str] = {}
        if city and country:
            times = await async_fetch_prayer_times(self.hass, city, country, method)
            _LOGGER.info("Prayer times for %s/%s: %s", city, country, times)

        dow = date.today().weekday()

        for entry in entries:
            prayer   = (entry.get("prayer") or "").lower()
            fixed    = entry.get("time", "")
            speakers = entry.get("speakers") or []
            url      = entry.get("url", "")
            vol      = int(entry.get("volume", 70))
            days     = entry.get("days", "daily")

            hhmm = times.get(prayer) if prayer else fixed
            if not hhmm or len(hhmm) < 4:
                continue
            if not _day_matches(days, dow):
                continue

            try:
                h, m = int(hhmm[:2]), int(hhmm[3:5])
            except ValueError:
                continue

            entry_copy = dict(entry)
            entry_copy["url"] = url
            entry_copy["volume"] = vol
            entry_copy["speakers"] = list(speakers)

            unsub = async_track_time_change(
                self.hass,
                lambda now, e=entry_copy: self.hass.async_create_task(self._fire(e)),
                hour=h, minute=m, second=0,
            )
            self._unsubs.append(unsub)
            _LOGGER.info(
                "Scheduled %s at %02d:%02d → %s",
                prayer or "fixed", h, m, speakers,
            )

    async def _fire(self, entry: dict[str, Any]) -> None:
        """Trigger the tannoy notify service for this prayer entry."""
        try:
            await self.hass.services.async_call(
                "notify",
                "lithe_tannoy",
                {
                    "message": entry["url"],
                    "data": {
                        "mode":     "start",
                        "volume":   int(entry["volume"]),
                        "speakers": entry["speakers"],
                    },
                },
                blocking=False,
            )
        except Exception as e:
            _LOGGER.error("Prayer fire failed: %s", e)


async def async_register_prayer_service(hass: HomeAssistant) -> None:
    """Register the lithe_audio.set_prayer_schedule service."""
    from homeassistant.core import ServiceCall

    async def svc_set_prayer_schedule(call: ServiceCall) -> None:
        # Tear down any existing schedule
        existing: PrayerScheduler | None = hass.data.get(DOMAIN, {}).get(DATA_PRAYER)
        if existing:
            await existing.async_shutdown()

        scheduler = PrayerScheduler(hass, dict(call.data))
        await scheduler.async_setup()
        hass.data.setdefault(DOMAIN, {})[DATA_PRAYER] = scheduler

    if not hass.services.has_service(DOMAIN, "set_prayer_schedule"):
        hass.services.async_register(DOMAIN, "set_prayer_schedule", svc_set_prayer_schedule)


async def async_unload_prayer(hass: HomeAssistant) -> None:
    sched: PrayerScheduler | None = hass.data.get(DOMAIN, {}).get(DATA_PRAYER)
    if sched:
        await sched.async_shutdown()
        hass.data[DOMAIN].pop(DATA_PRAYER, None)
