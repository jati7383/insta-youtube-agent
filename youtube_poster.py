"""
YouTube Shorts Poster Module
=============================
Posts reels as YouTube Shorts using YouTube Data API v3.
- 6 videos per day (API quota limit: 10,000 units, each upload = 1,600)
- Uses same CDN download as Instagram agent
- Auto-refreshes OAuth tokens
"""

import os
import json
import time
import logging
import requests
import tempfile
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("youtube_poster")

TOKEN_FILE = Path(__file__).parent / "youtube_token.json"
CLIENT_ID = "647867384633-nrr81ev58o25ddvtlcksju0gb4611rsb.apps.googleusercontent.com"
CLIENT_SECRET = "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"
TOKEN_URI = "https://oauth2.googleapis.com/token"

YT_SHORTS_PER_DAY = 7
YT_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
YT_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def load_tokens():
    """Load YouTube OAuth tokens from environment variable or file."""
    # Try environment variable first (for Railway)
    env_token = os.environ.get("YOUTUBE_TOKEN_JSON")
    if env_token:
        try:
            return json.loads(env_token)
        except Exception as e:
            logger.error(f"Failed to parse YOUTUBE_TOKEN_JSON env var: {e}")

    # Fallback to file
    if not TOKEN_FILE.exists():
        logger.error("YouTube token file not found and no env var set!")
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f)


def save_tokens(tokens):
    """Save updated tokens to file."""
    try:
        with open(TOKEN_FILE, "w") as f:
            json.dump(tokens, f, indent=2)
    except:
        pass


def refresh_access_token(tokens):
    """Refresh the access token using the refresh token."""
    resp = requests.post(TOKEN_URI, data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token"
    })
    if resp.status_code == 200:
        new_data = resp.json()
        tokens["access_token"] = new_data["access_token"]
        if "refresh_token" in new_data:
            tokens["refresh_token"] = new_data["refresh_token"]
        save_tokens(tokens)
        logger.info("YouTube access token refreshed")
        return tokens
    else:
        logger.error(f"Failed to refresh token: {resp.status_code} {resp.text}")
        return None


def yt_get_valid_token():
    """Get a valid access token, refreshing if needed."""
    tokens = load_tokens()
    if not tokens or "refresh_token" not in tokens:
        logger.warning("No refresh token found.")
        return None
    
    # Test if current token works
    resp = requests.get("https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
                       headers={"Authorization": f'Bearer {tokens["access_token"]}'})
    if resp.status_code == 401: # Token expired or invalid
        tokens = refresh_access_token(tokens)
        if not tokens:
            return None
    elif resp.status_code != 200:
        return None
    
    return tokens["access_token"]


def download_video(url, max_size_mb=50):
    """Download video to temp file. Returns path or None."""
    try:
        resp = requests.get(url, stream=True, timeout=60)
        if resp.status_code != 200:
            return None
        
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tmp.close()
        return tmp.name
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return None


def upload_to_youtube(video_path, title, description="", tags=None, category_id="22"):
    """Upload a video as a YouTube Short."""
    access_token = yt_get_valid_token()
    if not access_token:
        return False, "No valid token"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    if "#Shorts" not in title and "#shorts" not in title:
        title = f"{title} #Shorts"
    
    if len(title) > 90:
        title = title[:87] + "..."
    
    metadata = {
        "snippet": {
            "title": title,
            "description": description[:4500] + "\n\n#Shorts #AmazonFBA #Ecommerce #Business",
            "tags": tags or ["Amazon FBA", "Ecommerce", "Business", "Shorts"],
            "categoryId": category_id
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }
    
    resp = requests.post(YT_UPLOAD_URL, headers=headers, json=metadata)
    if resp.status_code != 200:
        return False, f"Init failed: {resp.status_code}"
    
    upload_url = resp.headers.get("Location")
    if not upload_url:
        return False, "No upload URL returned"
    
    file_size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        upload_resp = requests.put(upload_url, 
                                   data=f,
                                   headers={
                                       "Authorization": f"Bearer {access_token}",
                                       "Content-Type": "video/mp4",
                                       "Content-Length": str(file_size)
                                   },
                                   timeout=300)
    
    if upload_resp.status_code == 200:
        video_id = upload_resp.json().get("id", "unknown")
        return True, video_id
    else:
        return False, f"Upload failed: {upload_resp.status_code}"


def post_shorts_batch(reels_data, access_token, count=7):
    """Post a batch of reels as YouTube Shorts."""
    success = 0
    failed = 0
    
    for i, reel in enumerate(reels_data[:count]):
        video_url = reel.get("video_url")
        video_path = reel.get("path")
        caption = reel.get("caption", "Amazon FBA Tips")
        
        is_temp = False
        if not video_path and video_url:
            video_path = download_video(video_url)
            is_temp = True
            
        if not video_path:
            failed += 1
            continue
        
        try:
            title = caption.split("\n")[0][:90] if caption else "Amazon FBA Tips"
            title = title.replace("#", "").strip()
            if not title:
                title = "Amazon FBA Tips"
            
            ok, result = upload_to_youtube(video_path, title, caption)
            if ok:
                success += 1
            else:
                failed += 1
        finally:
            if is_temp:
                try:
                    os.unlink(video_path)
                except:
                    pass
        
        if i < count - 1:
            time.sleep(30)
    
    return success, failed
