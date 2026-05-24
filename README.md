# 🏛️ Ash & Empires — Video Generator

Automated dark history video pipeline. Generates and posts 90-second videos to YouTube Shorts, TikTok, and Instagram Reels daily.

---

## What It Does

1. Reads next topic from `topics.csv`
2. Generates a cinematic script using Claude API
3. Converts script to voiceover using ElevenLabs
4. Generates scene images using Flux (via fal.ai)
5. Transcribes audio into animated captions using AssemblyAI
6. Assembles everything into a finished video using Creatomate
7. Generates a thumbnail
8. Posts to YouTube, TikTok, and Instagram simultaneously
9. Logs everything for performance tracking

**Cost per video: ~$1.15**
**Time per video: ~8-12 minutes**

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Get your API keys

| Service | URL | Free Tier |
|---|---|---|
| Anthropic (Claude) | console.anthropic.com | Pay per use |
| ElevenLabs | elevenlabs.io | 10k chars/mo free |
| fal.ai (Flux images) | fal.ai | $1 free credit |
| AssemblyAI | assemblyai.com | $50 free credit |
| Creatomate | creatomate.com | 10 free renders |
| Ideogram | ideogram.ai | Free tier |

### 3. Fill in config.py
Open `config.py` and replace all `"your_xxx_key"` values with your actual API keys.

### 4. Set up YouTube API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project
3. Enable **YouTube Data API v3** and **YouTube Analytics API**
4. Create OAuth 2.0 credentials (Desktop app)
5. Download credentials JSON
6. Run this once to get your refresh token:

```python
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    'credentials.json',
    scopes=['https://www.googleapis.com/auth/youtube.upload',
            'https://www.googleapis.com/auth/youtube.readonly']
)
creds = flow.run_local_server(port=0)
print("REFRESH TOKEN:", creds.refresh_token)
```

Add the refresh token to `config.py`.

### 5. Set up TikTok API

1. Go to [TikTok for Developers](https://developers.tiktok.com)
2. Create an app and request **Content Posting API** access
3. Get your Client Key, Client Secret
4. Complete OAuth flow to get Access Token
5. Add all three to `config.py`

### 6. Set up Instagram API

Instagram requires videos to be hosted on a public URL before posting.

**Option A — Cloudinary (Recommended, free tier available):**
```bash
pip install cloudinary
```

Add to `config.py`:
```python
CLOUDINARY_CLOUD_NAME = "your_cloud_name"
CLOUDINARY_API_KEY = "your_api_key"
CLOUDINARY_API_SECRET = "your_api_secret"
```

Then in `modules/publisher.py`, replace the `upload_to_cdn` function:
```python
import cloudinary
import cloudinary.uploader

def upload_to_cdn(file_path: str) -> str:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET
    )
    result = cloudinary.uploader.upload(file_path, resource_type="video")
    return result["secure_url"]
```

1. Go to [Facebook Developers](https://developers.facebook.com)
2. Create an app, add Instagram Graph API
3. Get a long-lived access token
4. Get your Instagram Account ID
5. Add both to `config.py`

---

## Usage

```bash
# Generate and post next pending topic
python main.py

# Generate a standalone video by ID
python main.py --topic-id 2

# Generate a series (research, master timeline, and all episode scripts)
python main.py --topic-id 1 --build-series

# Rebuild series research
python main.py --topic-id 1 --build-series --rebuild-research

# Preview a specific episode of a series
python main.py --topic-id 1 --episode 3 --preview

# List all topics and their status
python main.py --list

# Update analytics (run 48hrs after posting)
python main.py --analytics

# Show performance summary
python main.py --summary

# List available ElevenLabs voices
python main.py --voices
```

---

## Adding Topics

Open `topics.csv` and add a new row. The schema supports both single videos and full series.

**For standalone videos:**
```
id,type,title,series_slug,episode_count,description,status
16,single,The Standalone Topic,,,1,Brief description,pending
```

**For a full series:**
```
id,type,title,series_slug,episode_count,description,status
1,series,The Fall of Carthage,the_fall_of_carthage,10,The full story of Carthage from Hannibal to destruction,pending
```

---

## Changing the Voice

1. Run `python main.py --voices` to see all available voices
2. Find a voice that sounds dramatic and authoritative
3. Copy its Voice ID
4. Update `ELEVENLABS_VOICE_ID` in `config.py`

**Recommended for dark history:**
- Daniel: `onwK4e9ZLuTAKqWW03F9`
- Liam: `TX3LPaxmHKxFdv7VOQHJ`
- Antoni: `ErXwobaYiN019PkySvjV`

---

## Automating Daily Posting

### Mac/Linux (cron):
```bash
# Open crontab
crontab -e

# Add this line to run every day at 9am
0 9 * * * cd /path/to/video-generator && python main.py >> logs/cron.log 2>&1
```

### Windows (Task Scheduler):
1. Open Task Scheduler
2. Create Basic Task
3. Set trigger: Daily at 9:00 AM
4. Action: `python C:\path\to\video-generator\main.py`

---

## Project Structure

```
video-generator/
├── main.py              # Entry point — run this
├── config.py            # All API keys and settings
├── requirements.txt     # Python dependencies
├── topics.csv           # Your content queue
├── modules/
│   ├── script.py        # Claude API → script generation
│   ├── voiceover.py     # ElevenLabs → MP3 voiceover
│   ├── images.py        # Flux → scene images
│   ├── captions.py      # AssemblyAI → timed captions
│   ├── assembly.py      # Creatomate → final video
│   ├── publisher.py     # Post to YT + TikTok + IG
│   └── tracker.py       # Performance logging
├── output/              # Final rendered videos and images (ignored by Git)
├── series_data/         # Source-of-truth series data and scripts (tracked by Git)
├── temp/                # Temporary files (auto-cleaned)
└── logs/
    └── performance.csv  # Analytics tracking
```

---

## Troubleshooting

**Script is too generic:**
Edit the system prompt in `modules/script.py` to be more specific about your style.

**Images don't match the story:**
The image prompts come from Claude. Improve the prompt in `script.py` to ask for more specific visual descriptions.

**Video assembly fails:**
Check your Creatomate API key and that all assets uploaded successfully. Creatomate logs are very detailed.

**TikTok upload fails:**
TikTok's Content Posting API requires business verification. If rejected, manually download from `output/` and post through TikTok Studio.

**Instagram CDN error:**
Set up Cloudinary (free tier is sufficient) as described in setup step 6.

---

## Performance Tips

- Post at **5-9pm EST** for maximum US/UK reach
- Review the first 3 videos manually before enabling full automation
- Check `logs/performance.csv` weekly to see which hooks perform best
- Update the Claude system prompt in `script.py` based on what works
