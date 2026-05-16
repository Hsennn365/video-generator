# ============================================
# ASH & EMPIRES — Video Generator Config
# ============================================# Fill in all API keys before running
ANTHROPIC_API_KEY = ""
ELEVENLABS_API_KEY = ""
ASSEMBLYAI_API_KEY = ""
CREATOMATE_API_KEY = ""
FAL_API_KEY = ""
STABILITY_API_KEY = ""
REPLICATE_API_KEY = ""
# Optional duplicate token used only for Replicate Flux; falls back to REPLICATE_API_KEY in images.py when empty.
REPLICATE_API_TOKEN = ""
REPLICATE_FLUX_MODEL = "black-forest-labs/flux-1.1-pro-ultra"
OPENAI_API_KEY = ""
SCRIPT_PROVIDER = "openai"
SCRIPT_MODEL = "gpt-4.1"

IMAGE_PROMPT_PROVIDER = "openai"
IMAGE_PROMPT_MODEL = "gpt-4.1"

# GPT Image generation
IMAGE_GENERATION_PROVIDER = "openai_gpt_image"
OPENAI_IMAGE_MODEL = "gpt-image-2"
OPENAI_IMAGE_SIZE = "1024x1536"
OPENAI_IMAGE_QUALITY = "medium"
OPENAI_IMAGE_MODERATION = "low"
OPENAI_IMAGE_OUTPUT_FORMAT = "jpeg"
SUPABASE_URL = ""
SUPABASE_KEY = ""
YOUTUBE_CLIENT_ID = "your_youtube_client_id"
YOUTUBE_CLIENT_SECRET = "your_youtube_client_secret"
YOUTUBE_REFRESH_TOKEN = "your_youtube_refresh_token"

TIKTOK_CLIENT_KEY = "your_tiktok_client_key"
TIKTOK_CLIENT_SECRET = "your_tiktok_client_secret"
TIKTOK_ACCESS_TOKEN = "your_tiktok_access_token"

INSTAGRAM_ACCESS_TOKEN = "your_instagram_access_token"
INSTAGRAM_ACCOUNT_ID = "your_instagram_account_id"

# ============================================
# ElevenLabs Voice Settings
# ============================================
# Best voices for dark history: Daniel, Liam, Antoni
ELEVENLABS_VOICE_ID = "onwK4e9ZLuTAKqWW03F9"  # Daniel voice

VOICE_SETTINGS = {
    "stability": 0.42,
    "similarity_boost": 0.85,
    "style": 0.70,
    "use_speaker_boost": True,
    "speed": 0.83
}

# ============================================
# Video Settings
# ============================================
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

# Shorter, tighter cinematic short
VIDEO_DURATION = 75

# More visual beats, but each beat is very short
SCENES_COUNT = 15

# Rough planning value only; final timing comes from captions/audio sync
SECONDS_PER_SCENE = 5

# ============================================
# Content Settings
# ============================================
CHANNEL_NAME = "Ash & Empires"
CHANNEL_NICHE = "Dark History & Lost Civilizations"
CONTENT_TONE = "dark, cinematic, mysterious, suspenseful, story-driven"
TARGET_AUDIENCE = "18-35 English speakers interested in cinematic history stories"
SERIES_NAME = "The Fall of Carthage"

# ============================================
# Paths
# ============================================
OUTPUT_DIR = "output"
TOPICS_FILE = "topics.csv"
LOGS_FILE = "logs/performance.csv"
TEMP_DIR = "temp"

# ============================================
# Music Settings
# ============================================
MUSIC_DIR = "music"
MUSIC_LIBRARY_FILE = "music/music_library.json"
DEFAULT_BACKGROUND_MUSIC_URL = ""



# ============================================
# Image Prompt + Image Generation Settings
# ============================================
IMAGE_PROMPT_REFINER_ENABLED = True
IMAGE_PROMPT_CACHE_ENABLED = True


# Options:
# "replicate_flux_ultra" = Replicate FLUX 1.1 Pro Ultra
# "stability_ultra" = Stability Ultra
# "stability_sd3" = Stability SD3

