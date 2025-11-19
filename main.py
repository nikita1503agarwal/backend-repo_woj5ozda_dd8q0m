import os
import time
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

app = FastAPI(title="UNBEQUEM/BEQUEM API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "YouTube helper backend running"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


# --- Database test remains (for environment diagnostics) ---
@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }

    try:
        from database import db

        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, "name") else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# ---------- YouTube Data API Integration ----------
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# simple in-memory cache
_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 20 * 60  # 20 minutes


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if not entry:
        return None
    if time.time() - entry["ts"] > CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return entry["data"]


def _cache_set(key: str, data: Any) -> None:
    _cache[key] = {"ts": time.time(), "data": data}


def _ensure_api_key():
    # Re-read in case environment changed after startup (e.g., on server restart)
    global YOUTUBE_API_KEY
    if not YOUTUBE_API_KEY:
        YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
    if not YOUTUBE_API_KEY:
        return False
    return True


def resolve_channel_id(handle: Optional[str] = None, channel_id: Optional[str] = None) -> str:
    if channel_id:
        return channel_id
    if not handle:
        raise HTTPException(status_code=400, detail="Provide either handle or channelId")

    # Try cache first
    cache_key = f"resolve:{handle}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    if not _ensure_api_key():
        # Fallback demo IDs if API key is missing, for graceful degradation
        fallback_map = {
            "@UNBEQUEM-o2w": "UC_demo_unbequem",
            "@BEQUEM-g": "UC_demo_bequem",
        }
        cid = fallback_map.get(handle, f"UC_demo_{handle.strip('@').lower()}")
        _cache_set(cache_key, cid)
        return cid

    # Use channels.list with forHandle
    url = f"{YOUTUBE_API_BASE}/channels"
    params = {"part": "id", "forHandle": handle, "key": YOUTUBE_API_KEY}
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"YouTube API error: {r.text[:200]}")
    data = r.json()
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=404, detail="Channel not found for handle")
    cid = items[0]["id"]
    _cache_set(cache_key, cid)
    return cid


def get_channel_statistics(channel_id: str) -> Dict[str, Any]:
    cache_key = f"stats:{channel_id}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    if not _ensure_api_key():
        demo = {"subscriberCount": 12345, "viewCount": 987654, "videoCount": 2, "title": "Demo Channel"}
        _cache_set(cache_key, demo)
        return demo

    url = f"{YOUTUBE_API_BASE}/channels"
    params = {"part": "statistics,snippet", "id": channel_id, "key": YOUTUBE_API_KEY}
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"YouTube API error: {r.text[:200]}")
    data = r.json()
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=404, detail="Channel not found")
    item = items[0]
    stats = item.get("statistics", {})
    snippet = item.get("snippet", {})
    out = {
        "subscriberCount": int(stats.get("subscriberCount", 0)),
        "viewCount": int(stats.get("viewCount", 0)),
        "videoCount": int(stats.get("videoCount", 0)),
        "title": snippet.get("title"),
        "thumbnails": snippet.get("thumbnails", {}),
        "customUrl": snippet.get("customUrl"),
        "description": snippet.get("description"),
    }
    _cache_set(cache_key, out)
    return out


def get_uploads_playlist_id(channel_id: str) -> Optional[str]:
    cache_key = f"uploads:{channel_id}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    if not _ensure_api_key():
        demo = f"PL_demo_{channel_id}"
        _cache_set(cache_key, demo)
        return demo

    url = f"{YOUTUBE_API_BASE}/channels"
    params = {"part": "contentDetails", "id": channel_id, "key": YOUTUBE_API_KEY}
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"YouTube API error: {r.text[:200]}")
    items = r.json().get("items", [])
    if not items:
        return None
    pid = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    _cache_set(cache_key, pid)
    return pid


def get_latest_videos(channel_id: str, max_results: int = 6) -> List[Dict[str, Any]]:
    cache_key = f"latest:{channel_id}:{max_results}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    if not _ensure_api_key():
        demo = [
            {
                "id": f"demo_video_{i}",
                "title": f"Demo Video {i}",
                "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
                "publishedAt": "2024-01-01T00:00:00Z",
                "viewCount": 0,
            }
            for i in range(1, max_results + 1)
        ]
        _cache_set(cache_key, demo)
        return demo

    url = f"{YOUTUBE_API_BASE}/search"
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "order": "date",
        "type": "video",
        "maxResults": max_results,
        "key": YOUTUBE_API_KEY,
    }
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"YouTube API error: {r.text[:200]}")
    items = r.json().get("items", [])
    videos = [
        {
            "id": it["id"]["videoId"],
            "title": it["snippet"]["title"],
            "thumbnail": it["snippet"]["thumbnails"].get("high", it["snippet"]["thumbnails"].get("medium", {})).get("url"),
            "publishedAt": it["snippet"]["publishedAt"],
        }
        for it in items
    ]
    _cache_set(cache_key, videos)
    return videos


def get_popular_videos(channel_id: str, max_results: int = 6) -> List[Dict[str, Any]]:
    cache_key = f"popular:{channel_id}:{max_results}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    if not _ensure_api_key():
        demo = [
            {
                "id": f"popular_demo_{i}",
                "title": f"Popular Demo {i}",
                "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
                "viewCount": 1000 * (max_results - i + 1),
            }
            for i in range(1, max_results + 1)
        ]
        _cache_set(cache_key, demo)
        return demo

    # Approach: search for videos, then query videos.list for statistics to sort by viewCount
    search_url = f"{YOUTUBE_API_BASE}/search"
    params = {
        "part": "id",
        "channelId": channel_id,
        "order": "date",
        "type": "video",
        "maxResults": 25,  # get a pool then sort
        "key": YOUTUBE_API_KEY,
    }
    r = requests.get(search_url, params=params, timeout=10)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"YouTube API error: {r.text[:200]}")
    ids = [it["id"]["videoId"] for it in r.json().get("items", []) if it.get("id", {}).get("videoId")]
    if not ids:
        return []

    videos_url = f"{YOUTUBE_API_BASE}/videos"
    params2 = {"part": "snippet,statistics", "id": ",".join(ids), "key": YOUTUBE_API_KEY}
    r2 = requests.get(videos_url, params=params2, timeout=10)
    if r2.status_code != 200:
        raise HTTPException(status_code=502, detail=f"YouTube API error: {r2.text[:200]}")
    items = r2.json().get("items", [])
    enriched = []
    for it in items:
        stats = it.get("statistics", {})
        snippet = it.get("snippet", {})
        enriched.append(
            {
                "id": it.get("id"),
                "title": snippet.get("title"),
                "thumbnail": snippet.get("thumbnails", {}).get("high", snippet.get("thumbnails", {}).get("medium", {})).get("url"),
                "viewCount": int(stats.get("viewCount", 0)),
                "publishedAt": snippet.get("publishedAt"),
            }
        )
    enriched.sort(key=lambda x: x.get("viewCount", 0), reverse=True)
    popular = enriched[: max_results]
    _cache_set(cache_key, popular)
    return popular


@app.get("/api/youtube/overview")
def youtube_overview(
    handle: Optional[str] = Query(default=None, description="Channel handle like @UNBEQUEM-o2w"),
    channelId: Optional[str] = Query(default=None, description="Channel ID if known"),
    maxResults: int = Query(default=6, ge=1, le=12),
):
    """Return subscribers, latest and popular videos for a channel."""
    cid = resolve_channel_id(handle=handle, channel_id=channelId)
    stats = get_channel_statistics(cid)
    latest = get_latest_videos(cid, max_results=maxResults)
    popular = get_popular_videos(cid, max_results=maxResults)
    return {"channelId": cid, "stats": stats, "latest": latest, "popular": popular}


@app.get("/api/youtube/subscribers")
def youtube_subscribers(handle: Optional[str] = None, channelId: Optional[str] = None):
    cid = resolve_channel_id(handle=handle, channel_id=channelId)
    stats = get_channel_statistics(cid)
    return {"channelId": cid, "subscriberCount": stats.get("subscriberCount", 0)}


@app.get("/api/youtube/videos/latest")
def youtube_latest(handle: Optional[str] = None, channelId: Optional[str] = None, maxResults: int = 6):
    cid = resolve_channel_id(handle=handle, channel_id=channelId)
    vids = get_latest_videos(cid, max_results=maxResults)
    return {"channelId": cid, "videos": vids}


@app.get("/api/youtube/videos/popular")
def youtube_popular(handle: Optional[str] = None, channelId: Optional[str] = None, maxResults: int = 6):
    cid = resolve_channel_id(handle=handle, channel_id=channelId)
    vids = get_popular_videos(cid, max_results=maxResults)
    return {"channelId": cid, "videos": vids}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
