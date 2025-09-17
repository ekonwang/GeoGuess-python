# file: example_streetview_flow.py
import requests
import os
from create_session import create_session_token

API_KEY = os.getenv("GOOGLE_API_KEY", None)

# 1) 获取会话令牌（mapType 必须是 streetview）
token = create_session_token(API_KEY, map_type="streetview", language="zh-CN", region="CN")
print("Session:", token.session, "Expires:", token.expires_at)

s = requests.Session()

# 2) 批量查 panoId（最多 100 个点）
pano_ids_url = "https://tile.googleapis.com/v1/streetview/panoIds"
payload = {
    "locations": [
        {"lat": 25.033964, "lng": 121.564468},  # 台北 101 附近
        {"lat": 25.047924, "lng": 121.517081},  # 台北车站
    ],
    "radius": 50,
}
resp = s.post(
    pano_ids_url,
    params={"session": token.session, "key": API_KEY},
    json=payload,
    timeout=15,
)
resp.raise_for_status()
pano_ids = resp.json().get("panoIds", [])
print("panoIds:", pano_ids)

# 3) 取元数据（也可以直接用 lat/lng+radius）
metadata_url = "https://tile.googleapis.com/v1/streetview/metadata"
meta = s.get(
    metadata_url,
    params={"session": token.session, "key": API_KEY, "panoId": pano_ids[0]},
    timeout=15,
).json()
print("metadata:", meta)

# 4) 取缩略图（JPEG）
thumb_url = "https://tile.googleapis.com/v1/streetview/thumbnail"
thumb = s.get(
    thumb_url,
    params={"session": token.session, "key": API_KEY, "panoId": pano_ids[0], "width": 250, "height": 150},
    timeout=15,
)
with open("thumb.jpg", "wb") as f:
    f.write(thumb.content)
print("Saved thumb.jpg")

# 5) 取某一层街景图块（示意：实际需要按照你的全景查看器坐标系来拼贴）
tiles_url = "https://tile.googleapis.com/v1/streetview/tiles"
# 例如，面片参数：panoId + zoom + x + y（实际参数以文档和查看器实现为准）
img = s.get(
    tiles_url,
    params={"session": token.session, "key": API_KEY, "panoId": pano_ids[0], "zoom": 3, "x": 0, "y": 0},
    timeout=15,
)
print("status:", img.status_code, "len:", len(img.content), "ct:", img.headers.get("content-type"))
with open("tile_0_0.jpg", "wb") as f:
    f.write(img.content)
print("Saved tile_0_0.jpg")
