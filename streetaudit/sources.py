"""Street-level image sources.

`GoogleStreetView` is the only source so far. Anything with the same two methods can replace it
(own 360-degree imagery, Mapillary): the rest of the tool does not know where the images come from.
"""
from __future__ import annotations

import time

import requests

META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"


class GoogleStreetView:
    own_copyright = "© Google"   # panoramas with another copyright are user photos with unreliable positions

    def __init__(self, key: str, session: requests.Session | None = None):
        self._key = key
        self._session = session or requests.Session()

    def nearest_panorama(self, lat: float, lon: float, radius_m: float) -> dict | None:
        """The outdoor panorama nearest to a point, or None. A free metadata request: no image is fetched."""
        # the radius must be a whole number: "10.0" is answered with INVALID_REQUEST
        params = {"location": f"{lat:.7f},{lon:.7f}", "radius": int(round(radius_m)), "source": "outdoor",
                  "key": self._key}
        for attempt in range(4):
            try:
                data = self._session.get(META_URL, params=params, timeout=15).json()
            except (requests.RequestException, ValueError):
                time.sleep(1.5 * (attempt + 1))
                continue
            status = data.get("status")
            if status == "OK":
                return {"pano_id": data["pano_id"], "lat": data["location"]["lat"], "lon": data["location"]["lng"],
                        "date": data.get("date"), "own": data.get("copyright") == self.own_copyright}
            if status in ("OVER_QUERY_LIMIT", "UNKNOWN_ERROR"):
                time.sleep(1.5 * (attempt + 1))
                continue
            if status in ("REQUEST_DENIED", "INVALID_REQUEST"):
                # a bad key or a bad request would otherwise look like "no imagery anywhere"
                raise RuntimeError(f"Street View metadata request failed: {status} {data.get('error_message', '')}")
            return None                # ZERO_RESULTS / NOT_FOUND: no panorama here
        return None

    def image(self, pano_id: str, heading: float, fov: float, pitch: float, size: int) -> bytes | None:
        """A perspective image from one panorama, asked for by panorama id so the camera position is known."""
        params = {"size": f"{size}x{size}", "pano": pano_id, "heading": heading, "fov": fov, "pitch": pitch,
                  "return_error_code": "true", "key": self._key}
        for attempt in range(4):
            try:
                r = self._session.get(IMAGE_URL, params=params, timeout=30)
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return r.content
            if r.status_code in (429, 500, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        return None

    @staticmethod
    def viewer_url(pano_id: str, heading: float, fov: float, pitch: float, lat: float | None = None,
                   lon: float | None = None) -> str:
        """A link that opens the free Street View website on this panorama, looking this way.

        With the camera position as `viewpoint`, the website falls back to the nearest panorama when it
        no longer knows the id (ids of recent imagery change); without it a stale id opens in the ocean.
        """
        url = (f"https://www.google.com/maps/@?api=1&map_action=pano&pano={pano_id}"
               f"&heading={heading}&pitch={pitch}&fov={min(fov, 100)}")
        if lat is not None and lon is not None:
            url += f"&viewpoint={lat:.7f},{lon:.7f}"
        return url
