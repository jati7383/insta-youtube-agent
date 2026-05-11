"""
Instagram Smart Reposting Agent v3.0 (Triple-Platform)
=======================================================
For: @amazo_nalpha (Jatinder Kumar)
"""
import os, json, random, time, logging, requests, schedule, tempfile, subprocess
from smart_scorer import score_and_rank_reels, smart_select_reels
from get_high_performing_insta_reels import get_high_performing_insta_reels
from youtube_poster import post_shorts_batch, yt_get_valid_token
from tiktok_poster import post_tiktok_batch
from datetime import datetime, timedelta
from pathlib import Path

INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get("IG_ACCOUNT_ID", "17841453907522432")
ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "EAAR7OhIjDr4BRNiyKaf7ZBxiRD7ZCIXKrDtZCdlNQS7rkAGcmfVSZAeWZANqSq13cZCfACOBSP91b9soJvtZA7oLZAG59nZC9knBZBZCIZBUJc5wpoBlqrxWmgqmZBtQyFhpakzJZA0sj0s38azzn2J4eU5NWxSNUCgcjXdeLKZBHeq7MKCpFIrWMsnHYpydlq8lj2r1BGL6AGGo37KvZCSoEOqI")
APP_ID = os.environ.get("IG_APP_ID", "1261389249449662")
APP_SECRET = os.environ.get("IG_APP_SECRET", "4405773fb8cb47fbcdca7e1cd3e1e7a4")
GRAPH_API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
POSTING_TIMES_UTC = ["16:00", "23:00", "04:00", "10:00"]
REELS_PER_CYCLE = 7
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "repost_database.json"
TOKEN_FILE = DATA_DIR / "current_token.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def get_token():
    if TOKEN_FILE.exists(): return TOKEN_FILE.read_text().strip()
    return ACCESS_TOKEN

def run_posting_cycle():
    logger.info(f"STARTING CYCLE: {datetime.now()}")
    insta_reels = get_high_performing_insta_reels(INSTAGRAM_BUSINESS_ACCOUNT_ID, get_token(), BASE_URL, min_views=500, count=REELS_PER_CYCLE)
    reels_to_post = [{"type": "existing_insta", "data": r} for r in insta_reels]
    random.shuffle(reels_to_post)
    
    yt_batch = []
    tt_batch = []
    for item in reels_to_post[:REELS_PER_CYCLE]:
        reel_data = item["data"]
        video_path = download_video(reel_data["media_url"], reel_data.get("id", "temp"))
        if video_path:
            cdn_url = upload_to_public_cdn(video_path)
            if cdn_url and publish_reel(cdn_url, reel_data["caption"]):
                yt_batch.append({"path": video_path, "caption": reel_data["caption"]})
                tt_batch.append({"path": video_path, "caption": reel_data["caption"]})
    
    if yt_batch: post_shorts_batch(yt_batch, yt_get_valid_token())
    if tt_batch: post_tiktok_batch(tt_batch)

def download_video(url, rid):
    p = os.path.join(tempfile.gettempdir(), f"r_{rid}.mp4")
    r = requests.get(url, stream=True)
    if r.status_code == 200:
        with open(p, "wb") as f: f.write(r.content)
        return p
    return None

def upload_to_public_cdn(p):
    with open(p, 'rb') as f:
        r = requests.post('https://litterbox.catbox.moe/resources/internals/api.php', files={'fileToUpload': f, 'reqtype': (None, 'fileupload'), 'time': (None, '1h')})
        return r.text.strip() if r.status_code == 200 else None

def publish_reel(url, cap):
    r = requests.post(f"{BASE_URL}/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media", params={"media_type": "REELS", "video_url": url, "caption": cap, "access_token": get_token()})
    cid = r.json().get("id")
    if not cid: return None
    for _ in range(20):
        time.sleep(5)
        if "FINISHED" in requests.get(f"{BASE_URL}/{cid}", params={"fields": "status", "access_token": get_token()}).json().get("status", ""):
            return requests.post(f"{BASE_URL}/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media_publish", params={"creation_id": cid, "access_token": get_token()}).json().get("id")
    return None

if __name__ == '__main__':
    if len(os.sys.argv) > 1 and os.sys.argv[1] == 'post': run_posting_cycle()
    else:
        schedule.every(6).hours.do(run_posting_cycle)
        while True: schedule.run_pending(); time.sleep(60)
