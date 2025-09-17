# file: tiles_session.py
from __future__ import annotations
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter, Retry

CREATE_SESSION_URL = "https://tile.googleapis.com/v1/createSession"

@dataclass(frozen=True)
class SessionToken:
    session: str
    expiry: int               # seconds since epoch, as returned by API
    tile_width: Optional[int] = None
    tile_height: Optional[int] = None
    image_format: Optional[str] = None

    @property
    def expires_at(self) -> float:
        """Return expiry as POSIX timestamp (float)."""
        return float(self.expiry)

    @property
    def seconds_left(self) -> float:
        return self.expires_at - time.time()

class MapTilesError(RuntimeError):
    pass

def _requests_session(timeout: float = 15.0) -> requests.Session:
    """
    Create a requests session with sensible retries.
    Retries on 429 and 5xx with exponential backoff.
    """
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    # stash default timeout on the session object
    s.request = _wrap_with_timeout(s.request, timeout)  # type: ignore
    return s

def _wrap_with_timeout(func, timeout: float):
    def wrapper(method, url, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout
        return func(method, url, **kwargs)
    return wrapper

def create_session_token(
    api_key: str,
    *,
    map_type: str = "streetview",
    language: str = "zh-CN",
    region: str = "CN",
    image_format: Optional[str] = None,       # "png" | "jpeg"
    scale: Optional[str] = None,              # "scaleFactor1x" | "scaleFactor2x" | "scaleFactor4x"
    high_dpi: Optional[bool] = None,
    layer_types: Optional[List[str]] = None,  # e.g. ["layerStreetview"]
    overlay: Optional[bool] = None,
    styles: Optional[List[Dict[str, Any]]] = None,
    session: Optional[requests.Session] = None,
) -> SessionToken:
    """
    Create a Map Tiles API session token (required for Street View Tiles).
    Docs: https://developers.google.com/maps/documentation/tile/session_tokens

    Raises:
        MapTilesError: on HTTP or protocol errors.
    """
    if not api_key:
        raise ValueError("api_key is required")
    if map_type not in {"roadmap", "satellite", "terrain", "streetview"}:
        raise ValueError("map_type must be one of: roadmap|satellite|terrain|streetview")
    payload: Dict[str, Any] = {
        "mapType": map_type,
        "language": language,
        "region": region,
    }
    if image_format is not None:
        if image_format not in {"png", "jpeg"}:
            raise ValueError("image_format must be 'png' or 'jpeg'")
        payload["imageFormat"] = image_format
    if scale is not None:
        if scale not in {"scaleFactor1x", "scaleFactor2x", "scaleFactor4x"}:
            raise ValueError("scale must be scaleFactor1x|scaleFactor2x|scaleFactor4x")
        payload["scale"] = scale
    if high_dpi is not None:
        payload["highDpi"] = bool(high_dpi)
    if layer_types:
        payload["layerTypes"] = list(layer_types)
    if overlay is not None:
        payload["overlay"] = bool(overlay)
    if styles:
        payload["styles"] = styles

    sess = session or _requests_session()
    params = {"key": api_key}
    headers = {"Content-Type": "application/json"}

    resp = sess.post(CREATE_SESSION_URL, params=params, headers=headers, data=json.dumps(payload))
    # Handle non-2xx
    if resp.status_code // 100 != 2:
        # Bubble up helpful message if possible
        try:
            err_json = resp.json()
        except Exception:
            err_json = {"error": resp.text}
        raise MapTilesError(f"createSession failed: HTTP {resp.status_code}: {err_json}")

    try:
        data = resp.json()
    except Exception as e:
        raise MapTilesError(f"Invalid JSON from createSession: {e}")

    # Validate protocol fields
    sess_token = data.get("session")
    expiry = data.get("expiry")
    if not sess_token or not isinstance(sess_token, str):
        raise MapTilesError("Missing 'session' in createSession response")
    if expiry is None:
        raise MapTilesError("Missing 'expiry' in createSession response")

    return SessionToken(
        session=sess_token,
        expiry=int(expiry),
        tile_width=data.get("tileWidth"),
        tile_height=data.get("tileHeight"),
        image_format=data.get("imageFormat"),
    )
