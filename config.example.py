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
SCRIPT_MODEL = "gpt-5.5"

IMAGE_PROMPT_PROVIDER = "openai"
IMAGE_PROMPT_MODEL = "gpt-5.5"

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
    "speed": 0.91
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
SERIES_DATA_DIR = "series_data"
TOPICS_FILE = "topics.csv"
LOGS_FILE = "logs/performance.csv"
TEMP_DIR = "temp"

# ============================================
# Research / Story Bible Settings
# ============================================
# Model used for automated series research (must support web_search tool).
# gpt-5.5 and gpt-4o both support the hosted web_search tool.
RESEARCH_MODEL = "gpt-5.5"

# If True, automatically generate research when no file is found for a series.
# If False, only use existing research files; skip research if none found.
RESEARCH_AUTO_GENERATE = True

# Directory where research JSON files are stored.
# These files are committed to Git as reusable source-of-truth data.
RESEARCH_DIR = "research"

# ============================================
# Music and Sound Effects Settings
# ============================================
MUSIC_DIR = "music"
MUSIC_LIBRARY_FILE = "music/music_library.json"
DEFAULT_BACKGROUND_MUSIC_URL = ""

# Manual Music Library Selection Configuration
# Modes: 
# - "fixed": Old behavior (always plays first music track in music_library.json)
# - "random": Choose any physically present track from library randomly
# - "tagged_random": Score tracks dynamically against script keywords and pick the best match
MUSIC_SELECTION_MODE = "tagged_random"
MUSIC_LIBRARY_DIR = "music/library"
DEFAULT_MUSIC_TAGS = ["dark", "cinematic", "history"]
BACKGROUND_MUSIC_VOLUME_MULTIPLIER = 0.18

# Local sound effects only (music/sfx/ — no downloads, no APIs)
ENABLE_SOUND_EFFECTS = True
SFX_LIBRARY_FILE = "music/sfx_library.json"
SFX_LIBRARY_DIR = "music/sfx"
MAX_SFX_PER_VIDEO = 3
SFX_VOLUME_MULTIPLIER = 0.08

# Assembly-only: skip thumbnail generation/regeneration when False
GENERATE_THUMBNAIL = True

# Legacy: motion on every scene (slow). Keep False.
ENABLE_IMAGE_MOTION = False

# Simple motion on every scene (fast, alternating zoom/pan pattern)
ENABLE_SIMPLE_ALL_SCENE_MOTION = True
# Only centered zoom in/out (odd scenes in, even scenes out). Pan types are ignored.
SIMPLE_MOTION_PATTERN = ["zoom_in", "zoom_out"]
SIMPLE_MOTION_AMOUNT = 0.02
SIMPLE_MOTION_FPS = 24
SIMPLE_MOTION_PRESET = "ultrafast"
SIMPLE_MOTION_USE_GPU_ENCODER = True
SIMPLE_MOTION_TIMEOUT_SECONDS = 180
SIMPLE_MOTION_TOTAL_TIMEOUT_SECONDS = 1200

# Professional editor-style motion (disabled when simple all-scene motion is on)
ENABLE_PROFESSIONAL_MOTION = False
PRO_MOTION_STYLE = "static_plus_push"
PRO_MOTION_MAX_SCENES = 3
PRO_MOTION_ZOOM_MAX = 1.015
PRO_MOTION_FPS = 24
PRO_MOTION_PRESET = "veryfast"
PRO_MOTION_USE_GPU_ENCODER = True
PRO_MOTION_TIMEOUT_SECONDS = 300
PRO_MOTION_TOTAL_TIMEOUT_SECONDS = 900

# Legacy key-scene settings (used when ENABLE_PROFESSIONAL_MOTION = False)
ENABLE_KEY_SCENE_MOTION = True
KEY_SCENE_MOTION_ONLY = True
KEY_SCENE_MOTION_MAX_SCENES = 3
KEY_SCENE_MOTION_INCLUDE_FIRST = True
KEY_SCENE_MOTION_INCLUDE_LAST = True
KEY_SCENE_MOTION_ZOOM_MAX = 1.03
KEY_SCENE_MOTION_FPS = 24
KEY_SCENE_MOTION_PRESET = "veryfast"
KEY_SCENE_MOTION_USE_GPU_ENCODER = True
KEY_SCENE_MOTION_TIMEOUT_SECONDS = 300
KEY_SCENE_MOTION_TOTAL_TIMEOUT_SECONDS = 900

# Cinematic transitions (primary movement feel — crossfade recommended)
ENABLE_SCENE_TRANSITIONS = True
SCENE_TRANSITION_TYPE = "crossfade"
SCENE_TRANSITION_DURATION = 0.35
SCENE_TRANSITION_RANDOMIZE = False
SCENE_TRANSITION_OPTIONS = ["fade", "crossfade", "dip_to_black"]



# ============================================
# Image Prompt + Image Generation Settings
# ============================================
IMAGE_PROMPT_REFINER_ENABLED = True
IMAGE_PROMPT_CACHE_ENABLED = True

# Image Prompt Refinement Modes:
# - "per_scene"   = Current behavior (refine each scene prompt individually via OpenAI call)
# - "global_only" = Generate one global visual plan, then use its final_prompt directly
#                   for each scene (saves substantial cost, no per-scene OpenAI calls)
# - "off"         = Use script scene image_prompt directly with style prefix (no planning/refinement calls)
# Default recommended setting:
IMAGE_PROMPT_REFINEMENT_MODE = "global_only"


# Options:
# "replicate_flux_ultra" = Replicate FLUX 1.1 Pro Ultra
# "stability_ultra" = Stability Ultra
# "stability_sd3" = Stability SD3

