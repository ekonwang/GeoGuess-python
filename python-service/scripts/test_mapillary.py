import math, os, requests

MAPILLARY_TOKEN = os.getenv("MAPILLARY_TOKEN")  # 先在环境变量里放你的 token

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dlat = math.radians(lat2-lat1)
    dlon = math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a))

def fetch_mapillary_image(lat, lon, max_radius_m=20, max_distance_m=10, min_size="thumb_1024_url"):
    # min_size: 可选 "thumb_1024_url" 或 "thumb_2048_url"
    url = "https://graph.mapillary.com/images"
    fields = f"id,computed_geometry,computed_compass_angle,{min_size},thumb_2048_url"
    params = {
        "access_token": MAPILLARY_TOKEN,
        "fields": fields,
        "closeto": f"{lon},{lat}",
        "radius": max_radius_m,
        "limit": 20,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        return None, "附近无街景影像"

    # 选离目标点最近且距离在阈值内的
    best = None
    best_d = 1e9
    for item in data:
        g = item.get("computed_geometry", {}).get("coordinates")
        if not g: 
            continue
        lon_i, lat_i = g
        d = haversine_m(lat, lon, lat_i, lon_i)
        if d < best_d:
            best_d = d
            best = item

    if best is None or best_d > max_distance_m:
        return None, f"找到影像，但最近距离 {int(best_d)} m > 阈值 {max_distance_m} m"

    # 取 2048 优先，否则退到 1024
    img_url = best.get("thumb_2048_url") or best.get(min_size)
    meta = {
        "image_id": best.get("id"),
        "distance_m": round(best_d, 2),
        "bearing_deg": best.get("computed_compass_angle"),  # 拍摄朝向（如有）
        "img_url": img_url,
    }
    return meta, None

# 使用示例
lat, lon = 40.4246, -74.0022  # 换成你的经纬度
meta, err = fetch_mapillary_image(lat, lon, max_radius_m=25, max_distance_m=10, min_size="thumb_1024_url")
print(err or meta)
