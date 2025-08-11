import os
import requests
from typing import Optional


GOOGLE_MAPS_API_KEY: Optional[str] = os.getenv("GOOGLE_MAPS_API_KEY")
NOMINATIM_BASE_URL: str = os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")


def get_http_session() -> requests.Session:
    session = requests.Session()
    # Nominatim requires a descriptive User-Agent per usage policy.
    session.headers.update({
        "User-Agent": os.getenv("USER_AGENT", "geoguess-python-service/1.0 (contact: dev@example.com)"),
        "Accept": "application/json",
    })
    return session 