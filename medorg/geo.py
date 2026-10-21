"""Offline reverse geocoding (GeoNames cities via the optional `reverse_geocoder` package)."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _rg():
    try:
        import reverse_geocoder

        return reverse_geocoder
    except Exception:
        return None


@lru_cache(maxsize=50_000)
def place(lat: float, lon: float) -> dict:
    """{'city': 'Bratislava', 'country': 'SK', 'region': 'Bratislavsky'} or {} when unavailable."""
    rg = _rg()
    if rg is None:
        return {}
    try:
        r = rg.search([(round(lat, 4), round(lon, 4))], mode=1, verbose=False)[0]
    except TypeError:  # older versions have no `verbose`
        r = rg.search([(round(lat, 4), round(lon, 4))], mode=1)[0]
    return {"city": r.get("name", ""), "country": r.get("cc", ""), "region": r.get("admin1", "")}


def available() -> bool:
    return _rg() is not None
