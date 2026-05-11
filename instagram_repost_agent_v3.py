"""
Instagram Smart Reposting Agent v2.2
======================================
For: @amazo_nalpha (Jatinder Kumar)

FEATURES:
- Analytics-driven smart scoring (saves/shares prioritized)
- China 2024 reels priority queue (19 reels over 7 days)
- CDN fallback chain: litterbox -> catbox -> manus-upload -> file.io
- 50% proven performers + 30% high-reach + 20% random diversity
- Caches scores to avoid re-fetching metrics every cycle
- Posts 21 reels per slot on Instagram, 4x daily (84/day)
- YouTube Shorts: 6 videos/day (posted in 2AM cycle)
- 30-day cooldown so no reel repeats within a month
"""

import os
import json
import random
import time
import logging
import requests
import schedule
import tempfile
import subprocess

from smart_scorer import score_and_rank_reels, smart_select_reels
from get_high_performing_reels import get_high_performing_curated_reels
from youtube_poster import post_shorts_batch, yt_get_valid_token

from datetime import datetime, timedelta
from pathlib import Path




# ============================================================
# CONFIGURATION
# ============================================================

INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get("IG_ACCOUNT_ID", "17841453907522432")
ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "EAAR7OhIjDr4BRNiyKaf7ZBxiRD7ZCIXKrDtZCdlNQS7rkAGcmfVSZAeWZANqSq13cZCfACOBSP91b9soJvtZA7oLZAG59nZC9knBZBZCIZBUJc5wpoBlqrxWmgqmZBtQyFhpakzJZA0sj0s38azzn2J4eU5NWxSNUCgcjXdeLKZBHeq7MKCpFIrWMsnHYpydlq8lj2r1BGL6AGGo37KvZCSoEOqI")
APP_ID = os.environ.get("IG_APP_ID", "1261389249449662")
APP_SECRET = os.environ.get("IG_APP_SECRET", "4405773fb8cb47fbcdca7e1cd3e1e7a4")
GRAPH_API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Posting schedule (Brisbane time = AEST = UTC+10)
# 2AM=16:00UTC, 9AM=23:00UTC, 2PM=04:00UTC, 8PM=10:00UTC
POSTING_TIMES_UTC = ["16:00", "23:00", "04:00", "10:00"]

# Reels per posting cycle
REELS_PER_CYCLE = 7

# YouTube Shorts per day (posted in the 2AM Brisbane cycle)
YT_SHORTS_PER_DAY = 7

# How many unscored reels to score per cycle
SCORE_SAMPLE_SIZE = 200

# Cooldown period (days)
COOLDOWN_DAYS = 30

# Database file
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "repost_database.json"
TOKEN_FILE = DATA_DIR / "current_token.txt"


# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(DATA_DIR / "instagram_agent.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================
# TOKEN MANAGEMENT
# ============================================================

def get_token():
    global ACCESS_TOKEN
    if TOKEN_FILE.exists():
        ACCESS_TOKEN = TOKEN_FILE.read_text().strip()
    return ACCESS_TOKEN


def save_token(token):
    global ACCESS_TOKEN
    ACCESS_TOKEN = token
    TOKEN_FILE.write_text(token)


def refresh_token_if_needed():
    token = get_token()
    try:
        resp = requests.get(
            f"{BASE_URL}/debug_token",
            params={"input_token": token, "access_token": f"{APP_ID}|{APP_SECRET}"}
        )
        data = resp.json().get("data", {})
        expires_at = data.get("expires_at", 0)
        now = int(time.time())

        if expires_at > 0 and (expires_at - now) < 86400 * 7:
            logger.info("Token expiring soon, attempting refresh...")
            refresh_resp = requests.get(
                f"{BASE_URL}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": APP_ID,
                    "client_secret": APP_SECRET,
                    "fb_exchange_token": token
                }
            )
            new_data = refresh_resp.json()
            if "access_token" in new_data:
                save_token(new_data["access_token"])
                logger.info(f"Token refreshed, expires in {new_data.get('expires_in', 'unknown')} seconds")
            else:
                logger.warning(f"Token refresh failed: {new_data}")
        else:
            days_left = (expires_at - now) / 86400 if expires_at > 0 else "unknown"
            logger.info(f"Token valid, ~{days_left} days remaining")
    except Exception as e:
        logger.error(f"Error checking token: {e}")


# ============================================================
# DATABASE FUNCTIONS
# ============================================================

def load_database():
    if DB_FILE.exists():
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {"posted_reels": {}, "all_media": {}, "reel_scores": {}, "last_scan": None, "original_posts": {}}


def save_database(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, default=str)


def mark_as_posted(db, media_id):
    db["posted_reels"][media_id] = datetime.now().isoformat()
    save_database(db)


# ============================================================
# CHINA REELS PRIORITY QUEUE
# ============================================================



# ============================================================
# INSTAGRAM API FUNCTIONS
# ============================================================
# INSTAGRAM API FUNCTIONS
# ============================================================

def fetch_all_media():
    logger.info("Fetching all media from Instagram account...")
    token = get_token()
    all_media = []
    url = f"{BASE_URL}/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media"
    params = {
        "fields": "id,media_type,media_url,thumbnail_url,permalink,caption,timestamp",
        "limit": 100,
        "access_token": token
    }

    page = 0
    while url:
        page += 1
        response = requests.get(url, params=params if page == 1 else None)
        if response.status_code != 200:
            logger.error(f"API Error: {response.status_code} - {response.text}")
            break

        data = response.json()
        if "error" in data:
            logger.error(f"API Error: {data['error']}")
            break

        media_items = data.get("data", [])
        all_media.extend(media_items)
        logger.info(f"Page {page}: Fetched {len(all_media)} media items")

        url = data.get("paging", {}).get("next")
        time.sleep(0.5)

    logger.info(f"Total media fetched: {len(all_media)}")
    return all_media

def publish_reel(video_url, caption):
    logger.info(f"Publishing reel with caption: {caption[:50]}...")
    token = get_token()
    
    # 1. Create Instagram Reel Container
    creation_id = None
    try:
        resp = requests.post(
            f"{BASE_URL}/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media",
            params={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": token
            }
        )
        resp.raise_for_status()
        creation_id = resp.json().get("id")
        logger.info(f"Reel container created with ID: {creation_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error creating reel container: {e}")
        if e.response:
            logger.error(f"Response: {e.response.text}")
        return None

    if not creation_id:
        return None

    # 2. Poll for Container Status
    status = "IN_PROGRESS"
    max_polls = 20  # Reduced to ~100 seconds total
    for _ in range(max_polls):
        time.sleep(5)
        try:
            resp = requests.get(
                f"{BASE_URL}/{creation_id}",
                params={
                    "fields": "status,status_code",
                    "access_token": token
                },
                timeout=10
            )
            resp.raise_for_status()
            status_data = resp.json()
            status = status_data.get("status", "").upper()
            status_code = status_data.get("status_code", "").upper()
            logger.info(f"Container {creation_id} status: {status} ({status_code})")

            if status == "FINISHED" or "FINISHED" in status:
                status = "FINISHED"
                break
            elif status == "FAILED" or "FAILED" in status:
                logger.error(f"Reel container creation failed: {status_code}")
                return None
        except Exception as e:
            logger.error(f"Error polling container status: {e}")
            # Continue polling instead of failing immediately
            continue
    
    if status != "FINISHED":
        logger.error(f"Reel container did not finish in time. Final status: {status}")
        return None

    # 3. Publish Container
    post_id = None
    try:
        resp = requests.post(
            f"{BASE_URL}/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media_publish",
            params={
                "creation_id": creation_id,
                "access_token": token
            }
        )
        resp.raise_for_status()
        post_id = resp.json().get("id")
        logger.info(f"Reel published with ID: {post_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error publishing reel: {e}")
        if e.response:
            logger.error(f"Response: {e.response.text}")
        return None
        
    return post_id


def publish_reel_from_url(video_url, caption):
    return publish_reel(video_url, caption)


# ============================================================
# VIDEO DOWNLOAD & RE-HOST FUNCTIONS (IMPROVED CDN)
# ============================================================

import tempfile
import subprocess

def download_video(media_url, reel_id):
    video_path = os.path.join(tempfile.gettempdir(), f"reel_{reel_id}.mp4")
    try:
        resp = requests.get(media_url, stream=True, timeout=120)
        if resp.status_code != 200:
            logger.error(f"Failed to download video: HTTP {resp.status_code}")
            return None
        with open(video_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        size_mb = os.path.getsize(video_path) / 1024 / 1024
        logger.info(f"Downloaded video: {size_mb:.1f} MB")
        return video_path
    except Exception as e:
        logger.error(f"Error downloading video: {e}")
        return None

def upload_to_public_cdn(video_path):
    """Upload video with CDN fallback chain: manus-upload -> litterbox -> catbox -> file.io"""
    
    # 1. Try manus-upload-file (S3) - Most reliable in this environment
    try:
        logger.info("Trying manus-upload-file (S3)...")
        result = subprocess.run(
            ["manus-upload-file", video_path],
            capture_output=True, text=True, timeout=180
        )
        output = result.stdout.strip()
        # Parse the CDN URL from output
        for line in output.splitlines():
            if line.startswith("Public URL:"):
                cdn_url = line.split(": ", 1)[1]
                logger.info(f"Uploaded to manus-upload-file: {cdn_url}")
                return cdn_url
        logger.warning(f"manus-upload-file output did not contain a public URL: {output}")
    except Exception as e:
        logger.error(f"manus-upload-file failed: {e}")

    # 2. Try litterbox.moe
    try:
        logger.info("Trying litterbox.moe...")
        with open(video_path, "rb") as f:
            files = {"file": f}
            resp = requests.post("https://litter.catbox.moe/moeupload.php", files=files, timeout=180)
            resp.raise_for_status()
            if resp.text.startswith("https://litter.catbox.moe/"):
                logger.info(f"Uploaded to litterbox.moe: {resp.text}")
                return resp.text
            else:
                logger.warning(f"litterbox.moe upload failed: {resp.text}")
    except Exception as e:
        logger.error(f"litterbox.moe upload failed: {e}")

    # 3. Try catbox.moe
    try:
        logger.info("Trying catbox.moe...")
        with open(video_path, "rb") as f:
            files = {"file": f}
            resp = requests.post("https://catbox.moe/user/api.php", data={
                "reqtype": "fileupload",
                "userhash": ""
            }, files=files, timeout=180)
            resp.raise_for_status()
            if resp.text.startswith("https://catbox.moe/"):
                logger.info(f"Uploaded to catbox.moe: {resp.text}")
                return resp.text
            else:
                logger.warning(f"catbox.moe upload failed: {resp.text}")
    except Exception as e:
        logger.error(f"catbox.moe upload failed: {e}")

    # 4. Try file.io
    try:
        logger.info("Trying file.io...")
        with open(video_path, "rb") as f:
            files = {"file": f}
            resp = requests.post("https://file.io/", files=files, timeout=180)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success") and data.get("link"):
                logger.info(f"Uploaded to file.io: {data['link']}")
                return data["link"]
            else:
                logger.warning(f"file.io upload failed: {data}")
    except Exception as e:
        logger.error(f"file.io upload failed: {e}")

    logger.error("All CDN upload attempts failed.")
    return None



def upload_to_public_cdn(video_path):
    """Upload video with CDN fallback chain: manus-upload -> litterbox -> catbox -> file.io"""
    
    # 1. Try manus-upload-file (S3) - Most reliable in this environment
    try:
        logger.info("Trying manus-upload-file (S3)...")
        result = subprocess.run(
            ['manus-upload-file', video_path],
            capture_output=True, text=True, timeout=180
        )
        output = result.stdout.strip()
        # Parse the CDN URL from output
        for line in output.split('\n'):
            line = line.strip()
            if line.startswith('http') and 'manuscdn.com' in line:
                logger.info(f"Uploaded to S3: {line}")
                return line
        # Try any URL in output
        for line in output.split('\n'):
            line = line.strip()
            if line.startswith('http'):
                logger.info(f"Uploaded to S3: {line}")
                return line
        logger.warning(f"manus-upload output: {output[:200]}")
    except Exception as e:
        logger.warning(f"manus-upload error: {e}")

    # 2. Try litterbox
    try:
        logger.info("Uploading to litterbox...")
        with open(video_path, 'rb') as f:
            resp = requests.post(
                'https://litterbox.catbox.moe/resources/internals/api.php',
                files={'fileToUpload': f},
                data={'reqtype': 'fileupload'},
                timeout=300
            )
            resp.raise_for_status()
            url = resp.text
            if url.startswith('http'):
                logger.info(f"Uploaded to litterbox: {url}")
                return url
            logger.warning(f"Litterbox response: {url[:200]}")
    except Exception as e:
        logger.warning(f"Litterbox error: {e}")

    # 3. Try catbox.moe
    try:
        logger.info("Trying catbox.moe...")
        with open(video_path, 'rb') as f:
            resp = requests.post(
                'https://catbox.moe/user/api.php',
                files={'fileToUpload': f},
                data={'reqtype': 'fileupload'},
                timeout=300
            )
            resp.raise_for_status()
            url = resp.text
            if url.startswith('http'):
                logger.info(f"Uploaded to catbox: {url}")
                return url
            logger.warning(f"Catbox response: {url[:200]}")
    except Exception as e:
        logger.warning(f"Catbox error: {e}")

    # 4. Try file.io
    try:
        logger.info("Trying file.io...")
        with open(video_path, 'rb') as f:
            resp = requests.post('https://file.io/', files={'file': f}, timeout=300)
            resp.raise_for_status()
            url = resp.json().get('link')
            if url:
                logger.info(f"Uploaded to file.io: {url}")
                return url
            logger.warning(f"File.io response: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"File.io error: {e}")

    logger.error("All CDN upload attempts failed.")
    return None


# ============================================================
# MAIN POSTING LOGIC
# ============================================================



# ============================================================
# VIDEO DOWNLOAD & RE-HOST FUNCTIONS (IMPROVED CDN)
# ============================================================



# ============================================================
# MAIN POSTING LOGIC
# ============================================================

from original_reel_generator import generate_original_reels
from youtube_poster import post_shorts_batch, YT_SHORTS_PER_DAY

def run_posting_cycle():
    """Execute one smart posting cycle with a mix of original and curated reels."""
    logger.info("=" * 60)
    logger.info("Starting SMART posting cycle v3.0 (Mixed Content)...")
    logger.info("=" * 60)
    
    refresh_token_if_needed()
    db = load_database()
    token = get_token()

    # PHASE 1: SELECTING REELS (MIX OF ORIGINAL AND CURATED)
    logger.info("PHASE 1: SELECTING REELS (MIX OF ORIGINAL AND CURATED)")
    
    # Get original reels
    original_reels = generate_original_reels(REELS_PER_CYCLE // 2 + REELS_PER_CYCLE % 2) # Get slightly more if odd
    
    # Get curated reels
    curated_reels = get_high_performing_curated_reels(num_reels=REELS_PER_CYCLE // 2)

    reels_to_post = []
    # Prioritize original reels if available, then fill with curated
    for reel in original_reels:
        reels_to_post.append({"type": "original", "data": reel})
    
    for reel in curated_reels:
        if len(reels_to_post) < REELS_PER_CYCLE:
            reels_to_post.append({"type": "curated", "data": reel})
        else:
            break

    random.shuffle(reels_to_post) # Mix them up
    reels_to_post = reels_to_post[:REELS_PER_CYCLE] # Ensure we don't exceed the limit

    if not reels_to_post:
        logger.warning("No reels selected for posting. Skipping cycle.")
        return

    posted_count = 0
    youtube_shorts_to_post = []

    for item in reels_to_post:
        if posted_count >= REELS_PER_CYCLE:
            break

        reel_type = item["type"]
        reel_data = item["data"]

        video_path = None
        caption = reel_data["caption"]
        reel_id = reel_data.get("id", f"orig_{int(time.time())}_{random.randint(1000,9999)}")

        if reel_type == "original":
            video_path = reel_data["path"]
            logger.info(f"Processing original reel {reel_id} for Instagram...")
        elif reel_type == "curated":
            media_url = reel_data["media_url"]
            logger.info(f"Processing curated reel {reel_id} for Instagram...")
            video_path = download_video(media_url, reel_id)
            if not video_path:
                logger.error(f"Failed to download curated reel {reel_id}. Skipping.")
                continue

        if video_path:
            cdn_url = upload_to_public_cdn(video_path)

            if cdn_url:
                instagram_post_id = publish_reel(cdn_url, caption)
                if instagram_post_id:
                    mark_as_posted(db, reel_id)
                    logger.info(f"Successfully posted {reel_type} reel {reel_id} to Instagram.")
                    youtube_shorts_to_post.append({"path": video_path, "caption": caption})
                    posted_count += 1
                else:
                    logger.error(f"Failed to post {reel_type} reel {reel_id} to Instagram.")
            else:
                logger.error(f"Failed to upload {reel_type} reel {reel_id} to CDN.")

    # PHASE 2: POSTING TO YOUTUBE SHORTS
    if youtube_shorts_to_post:
        logger.info("PHASE 2: POSTING TO YOUTUBE SHORTS")
        youtube_access_token = yt_get_valid_token()
        if youtube_access_token:
            post_shorts_batch(youtube_shorts_to_post, youtube_access_token)
        else:
            logger.error("Skipping YouTube Shorts posting due to invalid or missing token.")
    else:
        logger.info("No YouTube Shorts to post in this cycle.")

    logger.info("=" * 60)
    logger.info("CYCLE COMPLETE")
    logger.info("=" * 60)
    












def start_scheduler():
    """Start the automated scheduler for 4x daily smart posting."""
    logger.info("Starting Instagram Smart Repost Agent v3.0 (Mixed Content)...")
    logger.info("Posting 7 mixed content reels every 6 hours UTC to Instagram and YouTube")
    logger.info("Daily total: 28 mixed content reels to Instagram and YouTube")
    
    save_token(ACCESS_TOKEN)
    
    # Schedule to run every 6 hours
    schedule.every(6).hours.do(run_posting_cycle)
    logger.info("Scheduled posting every 6 hours UTC")
    
    logger.info("v3.0 scheduler is running. Press Ctrl+C to stop.")
    
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "post":
            save_token(ACCESS_TOKEN)
            run_posting_cycle()
        elif sys.argv[1] == "run":
            start_scheduler()
        else:
            print("Usage: python3 instagram_repost_agent_v3.py [post|run]")
    else:
        print("""
Instagram Smart Reposting Agent v3.0 (Mixed Content)pha
=======================================================
Commands:
  post - Run one full posting cycle (7 mixed content reels to Instagram and YouTube)
  run  - Start the full smart scheduler (4x daily, 7 reels/cycle)

Per cycle: 7 mixed content reels (Instagram & YouTube)
Daily: 28 reels (Instagram & YouTube)

""")
