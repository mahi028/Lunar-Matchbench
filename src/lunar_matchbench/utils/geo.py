"""
Geographic / spherical utilities for the lunar surface.

The Moon is treated as a sphere of mean radius 1737.4 km.
Coordinates are planetocentric, longitude 0-360 E.
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass

LUNAR_RADIUS_KM   = 1737.4
DEG_TO_KM_LAT     = np.pi * LUNAR_RADIUS_KM / 180.0   # km per degree of latitude


@dataclass
class BBox:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max

    def area_km2(self) -> float:
        mid_lat = (self.lat_min + self.lat_max) / 2.0
        d_lat = (self.lat_max - self.lat_min) * DEG_TO_KM_LAT
        d_lon = (self.lon_max - self.lon_min) * DEG_TO_KM_LAT * np.cos(np.radians(mid_lat))
        return d_lat * d_lon

    def intersect(self, other: "BBox") -> "BBox | None":
        lat_min = max(self.lat_min, other.lat_min)
        lat_max = min(self.lat_max, other.lat_max)
        lon_min = max(self.lon_min, other.lon_min)
        lon_max = min(self.lon_max, other.lon_max)
        if lat_max <= lat_min or lon_max <= lon_min:
            return None
        return BBox(lat_min, lat_max, lon_min, lon_max)

    def iou(self, other: "BBox") -> float:
        inter = self.intersect(other)
        if inter is None:
            return 0.0
        inter_area = inter.area_km2()
        union_area = self.area_km2() + other.area_km2() - inter_area
        return inter_area / union_area if union_area > 0 else 0.0


def overlap_report(ch2: BBox, lroc: BBox) -> dict:
    """Return a JSON-serialisable overlap summary dict."""
    inter = ch2.intersect(lroc)
    return {
        "has_overlap": inter is not None,
        "ch2_bbox": {"lat": [ch2.lat_min, ch2.lat_max], "lon": [ch2.lon_min, ch2.lon_max]},
        "lroc_bbox": {"lat": [lroc.lat_min, lroc.lat_max], "lon": [lroc.lon_min, lroc.lon_max]},
        "intersection_bbox": ({"lat": [inter.lat_min, inter.lat_max], "lon": [inter.lon_min, inter.lon_max]}
                              if inter else None),
        "overlap_area_km2": round(inter.area_km2(), 2) if inter else 0.0,
        "ch2_patch_area_km2": round(ch2.area_km2(), 2),
        "ch2_overlap_pct": round(inter.area_km2() / ch2.area_km2() * 100, 1) if inter and ch2.area_km2() > 0 else 0.0,
        "iou": round(ch2.iou(lroc), 4),
    }
