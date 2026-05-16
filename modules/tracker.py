import csv
import os
import json
import requests
from datetime import datetime
from config import LOGS_FILE, YOUTUBE_REFRESH_TOKEN, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET


def init_logs():
    """Initialize the performance log CSV if it doesn't exist."""
    os.makedirs(os.path.dirname(LOGS_FILE), exist_ok=True)

    if not os.path.exists(LOGS_FILE):
        with open(LOGS_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "topic_id", "title", "series", "part",
                "generated_at", "posted_at",
                "youtube_id", "tiktok_id", "instagram_id",
                "yt_views_48h", "yt_watch_time_48h", "yt_likes_48h",
                "tt_views_48h", "tt_likes_48h",
                "hook_style", "video_path"
            ])

    print("✅ Logs initialized")


def log_video(topic: dict, script: dict, publish_results: dict, video_path: str):
    """Log a newly published video to the performance tracker."""
    os.makedirs(os.path.dirname(LOGS_FILE), exist_ok=True)

    row = {
        "topic_id": topic["id"],
        "title": script["title"],
        "series": script.get("series", ""),
        "part": script.get("part", ""),
        "generated_at": datetime.now().isoformat(),
        "posted_at": datetime.now().isoformat(),
        "youtube_id": publish_results.get("youtube", {}).get("id", ""),
        "tiktok_id": publish_results.get("tiktok", {}).get("id", ""),
        "instagram_id": publish_results.get("instagram", {}).get("id", ""),
        "yt_views_48h": "",
        "yt_watch_time_48h": "",
        "yt_likes_48h": "",
        "tt_views_48h": "",
        "tt_likes_48h": "",
        "hook_style": script.get("hook", "")[:50],
        "video_path": video_path
    }

    with open(LOGS_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        writer.writerow(row)

    print(f"✅ Video logged: {script['title']}")


def fetch_youtube_analytics(video_id: str) -> dict:
    """Fetch 48-hour performance data from YouTube Analytics API."""
    # Refresh token
    token_response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "refresh_token": YOUTUBE_REFRESH_TOKEN,
            "grant_type": "refresh_token"
        }
    )
    access_token = token_response.json().get("access_token")

    response = requests.get(
        "https://youtubeanalytics.googleapis.com/v2/reports",
        params={
            "ids": "channel==MINE",
            "metrics": "views,estimatedMinutesWatched,likes",
            "dimensions": "video",
            "filters": f"video=={video_id}",
            "startDate": "2020-01-01",
            "endDate": datetime.now().strftime("%Y-%m-%d")
        },
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if response.status_code != 200:
        return {}

    rows = response.json().get("rows", [])
    if not rows:
        return {}

    return {
        "views": rows[0][1],
        "watch_minutes": rows[0][2],
        "likes": rows[0][3]
    }


def update_performance():
    """
    Run this 48 hours after posting to fetch and log performance data.
    Call from a separate cron job or manually.
    """
    if not os.path.exists(LOGS_FILE):
        print("No log file found")
        return

    rows = []
    with open(LOGS_FILE, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    updated = 0
    for row in rows:
        # Only update rows without analytics yet
        if row["yt_views_48h"] or not row["youtube_id"]:
            continue

        yt_data = fetch_youtube_analytics(row["youtube_id"])
        if yt_data:
            row["yt_views_48h"] = yt_data.get("views", "")
            row["yt_watch_time_48h"] = yt_data.get("watch_minutes", "")
            row["yt_likes_48h"] = yt_data.get("likes", "")
            updated += 1

    # Write updated rows back
    if rows and updated > 0:
        with open(LOGS_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    print(f"✅ Performance updated for {updated} videos")


def print_performance_summary():
    """Print a summary of top performing videos."""
    if not os.path.exists(LOGS_FILE):
        print("No performance data yet")
        return

    with open(LOGS_FILE, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["yt_views_48h"]]

    if not rows:
        print("No analytics data yet — run update_performance() after 48 hours")
        return

    sorted_rows = sorted(rows, key=lambda x: int(x["yt_views_48h"] or 0), reverse=True)

    print("\n📊 TOP PERFORMING VIDEOS:")
    print("-" * 60)
    for row in sorted_rows[:10]:
        print(f"  {row['title'][:40]}")
        print(f"  Views: {row['yt_views_48h']} | Watch time: {row['yt_watch_time_48h']} min | Likes: {row['yt_likes_48h']}")
        print(f"  Hook: {row['hook_style']}")
        print()
