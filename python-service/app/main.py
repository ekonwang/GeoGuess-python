from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import logging

from .config import GOOGLE_MAPS_API_KEY
from .geo import random_point_in_polygon, fetch_city_geojson
from .streetview import street_view_metadata, find_streetview_random, StreetViewError


app = FastAPI(title="GeoGuess Python Street View Service", version="1.0.0")

logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RandomPointRequest(BaseModel):
    geojson: Optional[Dict[str, Any]] = Field(default=None, description="Feature, FeatureCollection, or Geometry")
    count: int = Field(default=1, ge=1, le=100)


class StreetViewRandomRequest(BaseModel):
    geojson: Optional[Dict[str, Any]] = None
    city: Optional[str] = None
    country: Optional[str] = None
    all_panorama: bool = False
    optimise: bool = True
    max_attempts: int = Field(default=10, ge=1, le=1000)
    radius: Optional[int] = Field(default=None, ge=1, le=500000)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "google_api_key_configured": bool(GOOGLE_MAPS_API_KEY),
    }


@app.get("/streetview/metadata")
async def get_streetview_metadata(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
    radius: int = Query(50, ge=1, le=500000),
    all_panorama: bool = Query(False),
) -> Dict[str, Any]:
    try:
        return street_view_metadata(lat, lng, radius=radius, all_panorama=all_panorama)
    except Exception as e:
        logger.exception("/streetview/metadata failed")
        return {"error": str(e)}


@app.post("/random-point")
async def post_random_point(req: RandomPointRequest) -> Dict[str, Any]:
    if req.geojson is None:
        # Global random sampling not provided; pick using city or explicit area only in this endpoint
        from random import random
        lat = (os.urandom(8)[0] / 255.0) * 180 - 90 if hasattr(os, "urandom") else (random() * 180 - 90)
        lng = (os.urandom(8)[0] / 255.0) * 360 - 180 if hasattr(os, "urandom") else (random() * 360 - 180)
        return {"points": [{"lat": lat, "lng": lng}]}

    points = [
        dict(zip(["lat", "lng"], random_point_in_polygon(req.geojson)))
        for _ in range(req.count)
    ]
    return {"points": points}


@app.get("/city-geojson")
async def get_city_geojson(city: str = Query(...), country: Optional[str] = Query(None)) -> Dict[str, Any]:
    try:
        feature = fetch_city_geojson(city, country)
        return feature
    except Exception as e:
        logger.exception("/city-geojson failed for city=%s country=%s", city, country)
        return {"error": str(e)}


@app.post("/streetview/random")
async def post_streetview_random(req: StreetViewRandomRequest) -> Dict[str, Any]:
    try:
        result = find_streetview_random(
            geojson_area=req.geojson,
            city=req.city,
            country=req.country,
            all_panorama=req.all_panorama,
            optimise=req.optimise,
            max_attempts=req.max_attempts,
            radius=req.radius,
        )
        return result
    except Exception as e:
        logger.exception("/streetview/random failed for city=%s country=%s", req.city, req.country)
        return {"error": str(e)} 
        