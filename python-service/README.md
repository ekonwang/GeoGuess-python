# Python Street View Service

This subproject provides a small FastAPI service to:
- Fetch Google Street View metadata at or near a coordinate
- Pick random locations globally or inside a provided GeoJSON polygon
- "City mode": resolve a city boundary via Nominatim and select locations within it, ensuring the result has Street View coverage

## Prerequisites
- Python 3.10+
- A Google Maps API key with access to Street View Static API

## Setup
1. Create a virtual environment and install dependencies:

```bash
cd python-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Set environment variables:
- `GOOGLE_MAPS_API_KEY` (required) – your Google Maps API key
- `NOMINATIM_BASE_URL` (optional) – default `https://nominatim.openstreetmap.org`

You can also create a `.env` file in this folder with:

```
GOOGLE_MAPS_API_KEY=YOUR_KEY
NOMINATIM_BASE_URL=https://nominatim.openstreetmap.org
```

3. Run the service:

```bash
uvicorn app.main:app --reload --port 8001
```

## API
- `GET /health`: simple healthcheck

- `GET /streetview/metadata?lat=..&lng=..&radius=50&source=default`:
  Queries Google Street View Image Metadata API near the coordinate.

- `POST /random-point`:
  Body:
  - `geojson` (optional): Feature, FeatureCollection, or Geometry used to restrict point selection
  - `count` (optional, default 1)

  Returns random point(s) globally or within the supplied GeoJSON polygon(s).

- `GET /city-geojson?city=Shanghai&country=CN`:
  Resolve the city boundary using Nominatim and return the GeoJSON polygon.

- `POST /streetview/random`:
  Body:
  - `geojson` (optional): use this area for random selection
  - `city` (optional): name of city to constrain selection (ignored if `geojson` present)
  - `country` (optional): country hint for the city
  - `all_panorama` (optional, default false): include outdoor/default sources
  - `optimise` (optional, default true): require image date present in metadata
  - `max_attempts` (optional, default 10): attempts to find a covered location

  Returns a coordinate with Street View coverage and the associated metadata when found.

## Notes
- This mirrors the frontend logic: random within area polygons, with retries until Street View is available.
- For "city mode", the service first queries Nominatim for a city polygon, then samples inside it. 