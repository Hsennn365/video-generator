import requests
import os
import json
import time
from config import (
    YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN,
    TIKTOK_ACCESS_TOKEN,
    INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_ACCOUNT_ID
)


# ============================================
# YOUTUBE
# ============================================

def get_youtube_access_token() -> str:
    """Refresh YouTube OAuth token."""
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "refresh_token": YOUTUBE_REFRESH_TOKEN,
            "grant_type": "refresh_token"
        }
    )

    if response.status_code != 200:
        raise Exception(f"YouTube token refresh failed: {response.text}")

    return response.json()["access_token"]


def upload_to_youtube(video_path: str, script: dict, thumbnail_path: str = None) -> str:
    """
    Uploads video to YouTube as a Short.
    Returns the YouTube video ID.
    """
    access_token = get_youtube_access_token()

    # Build title with series info
    if script.get("series") and script.get("part"):
        title = f"{script['title']} | Part {script['part']} #Shorts"
    else:
        title = f"{script['title']} #Shorts"

    # Build tags from hashtags
    tags = script.get("hashtags", []) + ["shorts", "history", "darkhistory", "ancienthistory"]

    metadata = {
        "snippet": {
            "title": title[:100],  # YouTube title limit
            "description": script.get("youtube_description", "") + "\n\n#Shorts",
            "tags": tags[:500],  # YouTube tag limit
            "categoryId": "27"  # Education category
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    # Initiate upload
    init_response = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(os.path.getsize(video_path))
        },
        json=metadata
    )

    if init_response.status_code != 200:
        raise Exception(f"YouTube upload init failed: {init_response.text}")

    upload_url = init_response.headers["Location"]

    # Upload video file
    with open(video_path, "rb") as f:
        upload_response = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "video/mp4"
            },
            data=f
        )

    if upload_response.status_code not in [200, 201]:
        raise Exception(f"YouTube video upload failed: {upload_response.text}")

    video_id = upload_response.json()["id"]
    print(f"  ✅ YouTube: https://youtube.com/shorts/{video_id}")

    # Upload thumbnail if provided
    if thumbnail_path and os.path.exists(thumbnail_path):
        thumb_response = requests.post(
            f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "image/jpeg"
            },
            data=open(thumbnail_path, "rb")
        )
        if thumb_response.status_code == 200:
            print(f"  ✅ YouTube thumbnail uploaded")

    return video_id


# ============================================
# TIKTOK
# ============================================

def upload_to_tiktok(video_path: str, script: dict) -> str:
    """
    Uploads video to TikTok using Content Posting API.
    Returns TikTok post ID.
    """
    # Build caption with hashtags
    hashtags = " ".join([f"#{h}" for h in script.get("hashtags", [])])
    if script.get("series") and script.get("part"):
        caption = f"Part {script['part']}: {script['title']}\n\n{hashtags}"
    else:
        caption = f"{script['title']}\n\n{hashtags}"

    caption = caption[:2200]  # TikTok caption limit

    # Initialize upload
    init_response = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "post_info": {
                "title": caption,
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": os.path.getsize(video_path),
                "chunk_size": os.path.getsize(video_path),
                "total_chunk_count": 1
            }
        }
    )

    if init_response.status_code != 200:
        raise Exception(f"TikTok init failed: {init_response.text}")

    result = init_response.json()["data"]
    publish_id = result["publish_id"]
    upload_url = result["upload_url"]

    # Upload video
    with open(video_path, "rb") as f:
        video_data = f.read()

    upload_response = requests.put(
        upload_url,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{len(video_data)-1}/{len(video_data)}"
        },
        data=video_data
    )

    if upload_response.status_code not in [200, 201, 206]:
        raise Exception(f"TikTok upload failed: {upload_response.text}")

    print(f"  ✅ TikTok: Published (ID: {publish_id})")
    return publish_id


# ============================================
# INSTAGRAM
# ============================================

def upload_to_instagram(video_path: str, script: dict) -> str:
    """
    Uploads video to Instagram as a Reel using Graph API.
    Returns Instagram media ID.
    """
    # Build caption
    hashtags = " ".join([f"#{h}" for h in script.get("hashtags", [])])
    if script.get("series") and script.get("part"):
        caption = f"Part {script['part']}: {script['title']}\n\n{script.get('hook', '')}\n\n{hashtags}"
    else:
        caption = f"{script['title']}\n\n{script.get('hook', '')}\n\n{hashtags}"

    caption = caption[:2200]

    # Step 1: Create media container
    # Note: Instagram requires a publicly accessible video URL
    # You need to upload to a CDN first (e.g., S3, Cloudinary)
    # For now this uses a placeholder - see README for CDN setup
    print("  ⚠️  Instagram requires CDN hosting - see README.md for setup")

    # Placeholder - implement CDN upload here
    video_url = upload_to_cdn(video_path)

    container_response = requests.post(
        f"https://graph.facebook.com/v18.0/{INSTAGRAM_ACCOUNT_ID}/media",
        params={
            "video_url": video_url,
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": True,
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
    )

    if container_response.status_code != 200:
        raise Exception(f"Instagram container error: {container_response.text}")

    container_id = container_response.json()["id"]

    # Step 2: Wait for processing
    for _ in range(20):
        status_response = requests.get(
            f"https://graph.facebook.com/v18.0/{container_id}",
            params={
                "fields": "status_code",
                "access_token": INSTAGRAM_ACCESS_TOKEN
            }
        )
        status = status_response.json().get("status_code")

        if status == "FINISHED":
            break
        elif status == "ERROR":
            raise Exception("Instagram processing failed")

        time.sleep(5)

    # Step 3: Publish
    publish_response = requests.post(
        f"https://graph.facebook.com/v18.0/{INSTAGRAM_ACCOUNT_ID}/media_publish",
        params={
            "creation_id": container_id,
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
    )

    if publish_response.status_code != 200:
        raise Exception(f"Instagram publish error: {publish_response.text}")

    media_id = publish_response.json()["id"]
    print(f"  ✅ Instagram: Published (ID: {media_id})")
    return media_id


def upload_to_cdn(file_path: str) -> str:
    """
    Placeholder for CDN upload.
    Replace with Cloudinary, S3, or any public CDN.
    Instagram requires a publicly accessible URL.
    """
    # Example with Cloudinary:
    # import cloudinary.uploader
    # result = cloudinary.uploader.upload(file_path, resource_type="video")
    # return result["secure_url"]
    raise NotImplementedError(
        "CDN upload not configured. See README.md for Cloudinary setup instructions."
    )


# ============================================
# PUBLISH TO ALL PLATFORMS
# ============================================

def publish_all(video_path: str, script: dict, thumbnail_path: str = None) -> dict:
    """
    Publishes video to all three platforms.
    Returns dict of results per platform.
    """
    results = {}

    print("\n📱 Publishing to all platforms...")

    # YouTube
    try:
        yt_id = upload_to_youtube(video_path, script, thumbnail_path)
        results["youtube"] = {"success": True, "id": yt_id, "url": f"https://youtube.com/shorts/{yt_id}"}
    except Exception as e:
        print(f"  ❌ YouTube failed: {e}")
        results["youtube"] = {"success": False, "error": str(e)}

    # TikTok
    try:
        tt_id = upload_to_tiktok(video_path, script)
        results["tiktok"] = {"success": True, "id": tt_id}
    except Exception as e:
        print(f"  ❌ TikTok failed: {e}")
        results["tiktok"] = {"success": False, "error": str(e)}

    # Instagram
    try:
        ig_id = upload_to_instagram(video_path, script)
        results["instagram"] = {"success": True, "id": ig_id}
    except Exception as e:
        print(f"  ❌ Instagram failed: {e}")
        results["instagram"] = {"success": False, "error": str(e)}

    return results
