import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from config import STABILITY_API_KEY, TEMP_DIR

try:
    from config import OPENAI_API_KEY
except ImportError:
    OPENAI_API_KEY = ""

try:
    from config import OPENAI_IMAGE_MODEL
except ImportError:
    OPENAI_IMAGE_MODEL = "gpt-image-2"

try:
    from config import OPENAI_IMAGE_SIZE
except ImportError:
    OPENAI_IMAGE_SIZE = "1024x1536"

try:
    from config import OPENAI_IMAGE_QUALITY
except ImportError:
    OPENAI_IMAGE_QUALITY = "medium"

try:
    from config import OPENAI_IMAGE_MODERATION
except ImportError:
    OPENAI_IMAGE_MODERATION = "low"

try:
    from config import OPENAI_IMAGE_OUTPUT_FORMAT
except ImportError:
    OPENAI_IMAGE_OUTPUT_FORMAT = "jpeg"

try:
    from config import IMAGE_PROMPT_PROVIDER
except ImportError:
    IMAGE_PROMPT_PROVIDER = "openai"

try:
    from config import IMAGE_PROMPT_MODEL
except ImportError:
    IMAGE_PROMPT_MODEL = "gpt-5.5"

try:
    from config import IMAGE_PROMPT_REFINEMENT_MODE
except ImportError:
    IMAGE_PROMPT_REFINEMENT_MODE = "global_only"

try:
    from config import IMAGE_PROMPT_REFINER_ENABLED
except ImportError:
    IMAGE_PROMPT_REFINER_ENABLED = True

try:
    from config import IMAGE_PROMPT_CACHE_ENABLED
except ImportError:
    IMAGE_PROMPT_CACHE_ENABLED = True

try:
    from config import IMAGE_PROMPT_GLOBAL_PLANNING_ENABLED
except ImportError:
    IMAGE_PROMPT_GLOBAL_PLANNING_ENABLED = True

try:
    from config import IMAGE_GENERATION_PROVIDER
except ImportError:
    IMAGE_GENERATION_PROVIDER = "openai_gpt_image"

try:
    from config import REPLICATE_API_TOKEN
except ImportError:
    REPLICATE_API_TOKEN = ""

try:
    from config import REPLICATE_API_KEY as _CFG_REPLICATE_KEY
except ImportError:
    _CFG_REPLICATE_KEY = ""

try:
    from config import REPLICATE_FLUX_MODEL
except ImportError:
    REPLICATE_FLUX_MODEL = "black-forest-labs/flux-1.1-pro-ultra"

try:
    from config import REPLICATE_FLUX_RAW
except ImportError:
    REPLICATE_FLUX_RAW = True

try:
    from config import REPLICATE_SAFETY_TOLERANCE
except ImportError:
    REPLICATE_SAFETY_TOLERANCE = 5


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_IMAGE_GENERATION_URL = "https://api.openai.com/v1/images/generations"

STABLE_IMAGE_ULTRA_URL = "https://api.stability.ai/v2beta/stable-image/generate/ultra"
SD3_GENERATE_URL = "https://api.stability.ai/v2beta/stable-image/generate/sd3"


VISUAL_STYLE_VERSION = "dark_graphic_novel_v5_less_realistic"
MAX_IMAGE_GENERATION_ATTEMPTS = 4


STYLE_PREFIX = """
Cinematic graphic novel illustration, painterly historical artwork.
Stylized realism, not photorealistic. Broad shapes, highly readable silhouettes, poster-like composition.
Muted colors, deep blacks, bronze, smoke, parchment tones, and dark red accents.
Clean composition: 1 main subject, 1 supporting background element, 1 strong mood/light element.
Simplified faces, broad silhouettes, figures seen from medium or wide distance.
Believable but stylized ancient Roman / Carthaginian clothing, ships, weapons, and architecture.
Emphasis on helmets, shadows, silhouettes, side views, back views, and group shapes to avoid individual portrait focus.
No pores, no wrinkles, no hyperrealistic skin texture, no eyelashes, no photographic close-up faces.
No excessive linework, no tiny scratches, no dense cross-hatching, no busy armor engraving, no crowded backgrounds.
Premium illustrated dark-history storytelling style.
Vertical 9:16 mobile composition.
"""


NEGATIVE_PROMPT = """
photorealistic portrait, close-up face, photography, photorealism, ultra-real skin texture,
pores, wrinkles, eyelashes, AI-realistic actor face, crowded battlefield detail,
too many props, busy background, modern clothing, watches, bracelets, modern jewelry,
fake readable text, gibberish letters, logos, watermark, subtitles,
cute cartoon, childish, bright cheerful colors, bright comedy style, anime,
generic fantasy armor, medieval knight armor, horned helmets, monsters, dragons,
deformed anatomy, extra fingers, blurry, low quality,
modern map labels, readable text on maps
"""


def _load_json(path: str | Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.is_file():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _extract_response_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()

    pieces: list[str] = []

    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    pieces.append(text)

    text = "\n".join(pieces).strip()

    if not text:
        raise Exception(f"OpenAI response had no readable text: {payload}")

    return text


def _is_billing_or_quota_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return any(
        phrase in text
        for phrase in [
            "billing_hard_limit_reached",
            "insufficient_quota",
            "quota",
            "429",
            "insufficient funds",
            "balance",
            "billing",
            "limit reached",
        ]
    )


def _openai_text_response(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_output_tokens: int,
    temperature: float = 0.35,
    reasoning_effort: str | None = None,
) -> str:
    """
    Calls OpenAI Responses API for text generation.
    Handles GPT-5 family models (no temperature allowed).
    """
    if not str(OPENAI_API_KEY or "").strip():
        raise Exception("Set OPENAI_API_KEY for GPT prompt refinement.")

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Detect GPT-5 family
    is_gpt5 = "gpt-5" in model.lower() or "5.5" in model.lower()

    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
        "max_output_tokens": max_output_tokens,
    }

    if is_gpt5:
        # GPT-5 family models do not support temperature but support reasoning_effort
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
    else:
        payload["temperature"] = temperature

    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers=headers,
        json=payload,
        timeout=300,
    )

    if not is_gpt5 and response.status_code == 400 and "temperature" in response.text.lower():
        payload.pop("temperature", None)
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers=headers,
            json=payload,
            timeout=300,
        )

    if response.status_code != 200:
        err_msg = response.text[:2000]
        if _is_billing_or_quota_error(err_msg):
            print(f"  🛑 OpenAI API stop (billing/quota): {err_msg}")
            raise Exception(f"BILLING_OR_QUOTA_STOP: {err_msg}")
        raise Exception(
            f"OpenAI text error {response.status_code}: {err_msg}"
        )

    return _extract_response_text(response.json())


def _script_scene_context(script: dict) -> list[dict]:
    """
    Compact whole-video visual context.

    A scene prompt can repeat or drift if it is refined in isolation. This
    context makes prompt cache invalidation depend on the full scene set, so
    changing any scene regenerates the whole visual plan.
    """
    context = []

    for scene in script.get("scenes", []) or []:
        context.append(
            {
                "scene_number": scene.get("scene_number", ""),
                "narration_segment": scene.get("narration_segment", ""),
                "scene_description": scene.get("scene_description", ""),
                "image_prompt": scene.get("image_prompt", ""),
            }
        )

    return context


def _hash_visual_plan(script: dict) -> str:
    payload = {
        "title": script.get("title", ""),
        "hook": script.get("hook", ""),
        "full_narration": script.get("full_narration", ""),
        "scenes": _script_scene_context(script),
        "prompt_provider": IMAGE_PROMPT_PROVIDER,
        "prompt_model": IMAGE_PROMPT_MODEL,
        "image_provider": IMAGE_GENERATION_PROVIDER,
        "visual_style_version": VISUAL_STYLE_VERSION,
        "global_planning": bool(IMAGE_PROMPT_GLOBAL_PLANNING_ENABLED),
    }

    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _hash_scene(scene: dict, script: dict) -> str:
    payload = {
        "title": script.get("title", ""),
        "scene_number": scene.get("scene_number", ""),
        "narration_segment": scene.get("narration_segment", ""),
        "scene_description": scene.get("scene_description", ""),
        "image_prompt": scene.get("image_prompt", ""),
        "visual_plan_hash": _hash_visual_plan(script),
        "prompt_provider": IMAGE_PROMPT_PROVIDER,
        "prompt_model": IMAGE_PROMPT_MODEL,
        "image_provider": IMAGE_GENERATION_PROVIDER,
        "visual_style_version": VISUAL_STYLE_VERSION,
    }

    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _prompt_cache_path(prefix: str) -> str:
    return os.path.join(TEMP_DIR, f"{prefix}enhanced_prompts.json")


def _get_cached_enhanced_prompt(
    prefix: str,
    scene_number: int,
    scene_hash: str
) -> str | None:
    if not IMAGE_PROMPT_CACHE_ENABLED:
        return None

    cache = _load_json(_prompt_cache_path(prefix), default={}) or {}
    item = cache.get(str(scene_number))

    if not isinstance(item, dict):
        return None

    if item.get("hash") != scene_hash:
        return None

    prompt = item.get("prompt")
    return prompt if isinstance(prompt, str) and prompt.strip() else None


def _save_cached_enhanced_prompt(
    prefix: str,
    scene_number: int,
    scene_hash: str,
    prompt: str
) -> None:
    if not IMAGE_PROMPT_CACHE_ENABLED:
        return

    path = _prompt_cache_path(prefix)
    cache = _load_json(path, default={}) or {}

    cache[str(scene_number)] = {
        "hash": scene_hash,
        "prompt": prompt,
        "prompt_provider": IMAGE_PROMPT_PROVIDER,
        "prompt_model": IMAGE_PROMPT_MODEL,
        "image_provider": IMAGE_GENERATION_PROVIDER,
        "visual_style_version": VISUAL_STYLE_VERSION,
    }

    _save_json(path, cache)


def _save_retry_prompt_to_cache(
    prefix: str,
    scene_number: int,
    scene_hash: str,
    prompt: str,
) -> None:
    if not IMAGE_PROMPT_CACHE_ENABLED:
        return

    path = _prompt_cache_path(prefix)
    cache = _load_json(path, default={}) or {}

    cache[str(scene_number)] = {
        "hash": scene_hash,
        "prompt": prompt,
        "prompt_provider": IMAGE_PROMPT_PROVIDER,
        "prompt_model": IMAGE_PROMPT_MODEL,
        "image_provider": IMAGE_GENERATION_PROVIDER,
        "visual_style_version": VISUAL_STYLE_VERSION,
        "rewritten_after_failure": True,
    }

    _save_json(path, cache)


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _is_safety_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return any(
        phrase in text
        for phrase in [
            "nsfw",
            "safety",
            "moderation",
            "flagged",
            "content detected",
            "content policy",
        ]
    )



def _extract_json(raw: str) -> Any:
    """
    Extract JSON from a model response, even if it is wrapped in ```json fences.
    """
    raw = str(raw or "").strip()

    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
        raw = raw.strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start_obj = raw.find("{")
        end_obj = raw.rfind("}")
        start_arr = raw.find("[")
        end_arr = raw.rfind("]")

        if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
            return json.loads(raw[start_obj:end_obj + 1])

        if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
            return json.loads(raw[start_arr:end_arr + 1])

        raise


def _strip_markers(text: str) -> str:
    return re.sub(r"\[\[(pause|voice):[^\]]+\]\]", "", str(text or "")).strip()


def _scene_list_for_visual_plan(script: dict) -> list[dict]:
    rows = []

    for scene in sorted(
        script.get("scenes", []) or [],
        key=lambda s: _safe_int(s.get("scene_number", 0), 0),
    ):
        rows.append(
            {
                "scene_number": int(scene.get("scene_number", 0)),
                "narration_segment": _strip_markers(scene.get("narration_segment", "")),
                "scene_description": scene.get("scene_description", ""),
                "draft_image_prompt": scene.get("image_prompt", ""),
            }
        )

    return rows


def _get_cached_visual_plan(prefix: str, script: dict) -> dict | None:
    if not IMAGE_PROMPT_CACHE_ENABLED:
        return None

    cache = _load_json(_prompt_cache_path(prefix), default={}) or {}
    plan_hash = _hash_visual_plan(script)

    if cache.get("_visual_plan_hash") != plan_hash:
        return None

    scenes = script.get("scenes", []) or []
    for scene in scenes:
        scene_number = int(scene.get("scene_number", 0))
        scene_hash = _hash_scene(scene, script)
        item = cache.get(str(scene_number))
        if not isinstance(item, dict):
            return None
        if item.get("hash") != scene_hash:
            return None
        if not str(item.get("prompt", "") or "").strip():
            return None

    return cache


def _normalise_scene_prompt_plan(payload: Any, script: dict) -> dict[int, str]:
    """
    Accept either:
    - {"scene_prompts": [{"scene_number": 1, "final_prompt": "..."}]}
    - [{"scene_number": 1, "final_prompt": "..."}]
    """
    if isinstance(payload, dict):
        items = payload.get("scene_prompts") or payload.get("scenes") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    prompts: dict[int, str] = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        scene_number = _safe_int(item.get("scene_number"), 0)
        prompt = (
            item.get("final_prompt")
            or item.get("prompt")
            or item.get("image_prompt")
            or ""
        )

        if scene_number > 0 and str(prompt).strip():
            prompts[scene_number] = str(prompt).strip()

    missing = []
    for scene in script.get("scenes", []) or []:
        scene_number = int(scene.get("scene_number", 0))
        if scene_number not in prompts:
            missing.append(scene_number)

    if missing:
        raise Exception(f"Visual plan missing prompts for scenes: {missing}")

    return prompts


def _save_visual_plan_cache(
    *,
    prefix: str,
    script: dict,
    plan_payload: Any,
    prompts_by_scene: dict[int, str],
) -> None:
    path = _prompt_cache_path(prefix)
    cache = _load_json(path, default={}) or {}
    plan_hash = _hash_visual_plan(script)

    cache["_visual_plan_hash"] = plan_hash
    cache["_visual_style_version"] = VISUAL_STYLE_VERSION
    cache["_prompt_provider"] = IMAGE_PROMPT_PROVIDER
    cache["_prompt_model"] = IMAGE_PROMPT_MODEL
    cache["_image_provider"] = IMAGE_GENERATION_PROVIDER
    cache["_visual_strategy"] = (
        plan_payload.get("visual_strategy", {}) if isinstance(plan_payload, dict) else {}
    )

    for scene in script.get("scenes", []) or []:
        scene_number = int(scene.get("scene_number", 0))
        scene_hash = _hash_scene(scene, script)
        cache[str(scene_number)] = {
            "hash": scene_hash,
            "prompt": prompts_by_scene[scene_number],
            "prompt_provider": IMAGE_PROMPT_PROVIDER,
            "prompt_model": IMAGE_PROMPT_MODEL,
            "image_provider": IMAGE_GENERATION_PROVIDER,
            "visual_style_version": VISUAL_STYLE_VERSION,
            "visual_plan_hash": plan_hash,
            "generated_from_global_plan": True,
        }

        debug_path = os.path.join(
            TEMP_DIR,
            f"{prefix}scene_{scene_number:02d}_final_prompt.txt"
        )
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(prompts_by_scene[scene_number])

    _save_json(path, cache)

    plan_debug_path = os.path.join(TEMP_DIR, f"{prefix}visual_prompt_plan.json")
    _save_json(plan_debug_path, plan_payload)


def prepare_scene_image_prompts(
    script: dict,
    prefix: str,
    *,
    force: bool = False,
) -> None:
    """
    Generate/refine all scene image prompts together before image generation.

    This is the permanent fix for the "random poster per scene" problem:
    GPT sees the entire script and creates a coordinated visual storyboard in
    one call, then each scene image uses its locked prompt from that plan.
    """
    mode = str(IMAGE_PROMPT_REFINEMENT_MODE).lower().strip()
    if mode == "off":
        return

    if not IMAGE_PROMPT_REFINER_ENABLED or not IMAGE_PROMPT_GLOBAL_PLANNING_ENABLED:
        return

    os.makedirs(TEMP_DIR, exist_ok=True)

    if not force and _get_cached_visual_plan(prefix, script):
        print("  ⚡ Global visual prompt plan loaded from cache")
        return

    video_title = script.get("title", "")
    full_narration = script.get("full_narration", "")
    scenes = _scene_list_for_visual_plan(script)

    if not scenes:
        return

    system_prompt = """
You are the visual storyboard director for a premium dark-history short video.

You do NOT write one isolated prompt at a time.
You read the entire video script and create a coordinated image prompt plan for
all scenes together, so the final images feel like one continuous film.

Return ONLY valid JSON.
No markdown.
No explanation.
"""

    user_prompt = f"""
Create a coordinated visual prompt plan for the whole video.

Video title:
{video_title}

Full narration:
{full_narration}

Scene list:
{json.dumps(scenes, indent=2, ensure_ascii=False)}

GLOBAL VISUAL IDENTITY:
- Cinematic graphic novel illustration, painterly historical artwork (stylized realism, not photorealism).
- Broad shapes, highly readable silhouettes, poster-like compositions.
- Muted bronze, parchment, smoke, deep blacks, dark red accents.
- Stylized but believable ancient Roman / Carthaginian world.
- Clean composition: 1 main subject, 1 supporting background element, 1 strong mood/light element.
- Broad silhouettes, simplified faces, prefer figures seen from a medium/wide distance.
- Emphasize helmets, shadows, silhouettes, back views, side views, and group shapes instead of front-facing portraits.
- No pores, wrinkles, eyelashes, or overly detailed human faces; avoid photorealistic actor faces.
- No excessive linework, no tiny scratches, no dense cross-hatching, no busy armor engraving, no crowded backgrounds.
- Vertical 9:16 mobile composition.
- No text, no watermark, no modern objects.
- No gore, no blood, no nudity, no graphic injury.

MOST IMPORTANT PROBLEM TO SOLVE:
Avoid random close-up faces and disconnected poster images.
This video must feel like a single chronological visual story.

CHARACTER CONSISTENCY RULES:
- Do not create a new detailed face for every scene.
- Avoid close-up portraits unless the narration absolutely requires a specific person.
- Prefer silhouettes, back-view figures, side-view figures, hands, objects, architecture, walls, ships, maps, gates, markets, armies seen wide, and symbolic details.
- If Hannibal appears in multiple scenes, keep him consistent:
  Carthaginian commander, dark red cloak, bronze/leather armor, short dark beard, weary posture.
  Prefer back view, side view, or medium/wide framing; do not create a different front-facing portrait each time.
- If Scipio appears, keep him distinct:
  Roman commander in red cloak and Roman armor, shown in medium/wide framing.
- Roman senators should not become random repeated close-up faces; show them as groups, silhouettes, hands, Senate chamber, maps, decrees, or torchlit benches.

VISUAL VARIETY RULES:
- No two consecutive scenes should use the same composition type.
- Do not use close-up human faces in more than 2 scenes total.
- Use a balanced progression of:
  wide establishing shot, architecture/gates/walls, object detail, group silhouette, strategic map/table, ships/harbor, marketplace/economy, symbolic shadow.
- Do not repeat "people standing and looking serious" across scenes.
- Do not turn every scene into a battle.
- Do not add fire unless the narration mentions fire, burning, torches, flames, destruction, or ominous glow.
- Do not add ships unless the narration mentions ships, sea, harbor, sailing, fleet, coastline, or travel.
- Do not add maps unless the narration mentions planning, strategy, Rome watching, empire, territory, or officials.
- If a scene is about lack of supplies, show empty carts, broken equipment, tired army, closed gates.
- If a scene is about recovery/trade, show harbor, merchants, goods, coins, cloth, storage, ships.
- If a scene is about fear/revenge, show shadows, Senate chamber, maps, standards, hands, architecture, posture, atmosphere.

PROMPT LOCKING RULES:
For each scene, write a final_prompt that:
- matches ONLY that scene narration
- respects the global storyboard plan
- does not repeat the previous scene's composition
- keeps recurring characters visually consistent
- does not change the historical event
- includes exact subject/action/location/period
- includes visible objects, clothing, architecture, mood, lighting, camera/framing
- includes "Must show:" and "Must not show:"
- includes vertical 9:16 composition
- says "no text, no watermark, no modern objects"
- says "no gore, no blood, no nudity"

Return ONLY this JSON:

{{
  "visual_strategy": {{
    "overall_style": "one sentence",
    "recurring_characters": {{
      "hannibal": "locked visual description if relevant",
      "scipio": "locked visual description if relevant",
      "roman_senators": "locked visual direction if relevant"
    }},
    "recurring_motifs": ["motif 1", "motif 2"],
    "avoid_repetition_rules": ["rule 1", "rule 2"]
  }},
  "scene_prompts": [
    {{
      "scene_number": 1,
      "visual_role": "wide establishing / object detail / group silhouette / symbolic / etc",
      "locked_subject": "main subject that must not drift",
      "composition_note": "how this scene is visually distinct",
      "final_prompt": "production-ready prompt"
    }}
  ]
}}
"""

    print(f"  🧭 Generating global visual prompt plan for {len(scenes)} scenes...")
    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=IMAGE_PROMPT_MODEL,
        max_output_tokens=9000,
        temperature=0.25,
        reasoning_effort="medium",
    ).strip()

    payload = _extract_json(raw)
    prompts_by_scene = _normalise_scene_prompt_plan(payload, script)

    _save_visual_plan_cache(
        prefix=prefix,
        script=script,
        plan_payload=payload,
        prompts_by_scene=prompts_by_scene,
    )

    print(f"  ✅ Global visual prompt plan saved for {len(prompts_by_scene)} scenes")


def enhance_image_prompt(scene: dict, script: dict, prefix: str) -> str:
    scene_number = int(scene.get("scene_number", 0))
    scene_hash = _hash_scene(scene, script)
    mode = str(IMAGE_PROMPT_REFINEMENT_MODE).lower().strip()

    print(f"  [Image Prompt Mode] Current mode: {mode}")

    # 1. Mode: off — skip global visual planning and per-scene refinement
    if mode == "off":
        print(f"  ⚡ Scene {scene_number} prompt mode is 'off' — using script draft prompt directly")
        return str(scene.get("image_prompt", "")).strip()

    # 2. Mode: global_only — generate visual plan once, use planned final_prompt directly without per-scene refinement
    if mode == "global_only":
        if script and script.get("scenes"):
            prepare_scene_image_prompts(script, prefix, force=False)
            cached = _get_cached_enhanced_prompt(prefix, scene_number, scene_hash)
            if cached:
                print(f"  ⚡ Scene {scene_number} prompt loaded from global plan — no per-scene refinement call")
                return cached
        print(f"  ⚠️ Scene {scene_number} not found in global plan cache — using script draft prompt directly (global_only fallback)")
        return str(scene.get("image_prompt", "")).strip()

    # 3. Mode: per_scene (Current/Old behavior) — global plan, then refine individually if needed
    if script and script.get("scenes"):
        prepare_scene_image_prompts(script, prefix, force=False)
        cached = _get_cached_enhanced_prompt(prefix, scene_number, scene_hash)
        if cached:
            print(f"  ⚡ Scene {scene_number} prompt loaded from global visual plan")
            return cached

    cached = _get_cached_enhanced_prompt(prefix, scene_number, scene_hash)
    if cached:
        print(f"  ⚡ Scene {scene_number} enhanced prompt loaded from cache")
        return cached

    if not IMAGE_PROMPT_REFINER_ENABLED:
        return str(scene.get("image_prompt", "")).strip()

    # Fallback only: used if global planning is disabled or needs individual refinement
    video_title = script.get("title", "")
    full_narration = script.get("full_narration", "")
    narration_segment = scene.get("narration_segment", "")
    scene_description = scene.get("scene_description", "")
    draft_prompt = scene.get("image_prompt", "")

    system_prompt = """
You are a GPT Image prompt director for premium dark-history short videos.

You write prompts that GPT Image can follow cleanly.

Output only one final image prompt.
No markdown.
No explanation.
"""

    user_prompt = f"""
Create ONE production-ready GPT Image prompt.

The image must match the exact scene narration, NOT the whole video.

Visual identity:
Cinematic graphic novel illustration, painterly historical artwork.
Stylized realism, not photorealistic. Broad shapes, highly readable silhouettes, poster-like composition.
Muted bronze, parchment, smoke, deep blacks, dark red accents.
Clean composition: 1 main subject, 1 supporting background element, 1 strong mood/light element.
Broad silhouettes, simplified faces, prefer figures seen from a medium/wide distance.
Emphasize helmets, shadows, silhouettes, back views, side views, and group shapes instead of front-facing portraits.
No pores, wrinkles, eyelashes, or overly detailed human faces; avoid photorealistic actor faces.
No excessive linework, no tiny scratches, no dense cross-hatching, no busy armor engraving, no crowded backgrounds.
NOT photorealistic portrait.
NOT close-up face.
NOT ultra-real human skin texture.
NOT cute cartoon.
NOT anime school style.
NOT fantasy game concept art.

Video title:
{video_title}

Full narration context:
{full_narration}

Scene number:
{scene_number}

Exact scene narration:
{narration_segment}

Scene description:
{scene_description}

Draft visual prompt:
{draft_prompt}

CRITICAL RULES:
- Match ONLY this scene.
- One clear focal subject.
- The image must be understandable in less than one second.
- Avoid close-up faces unless absolutely required by the narration.
- Prefer silhouettes, side/back views, hands, maps, walls, ships, armies wide, gates, temples, objects, and atmosphere.
- Do not create a new detailed face for every scene.
- Do not add Roman soldiers unless this scene requires soldiers, legions, army, invasion, battle, or war.
- Do not add fire unless this scene requires fire, burning, destruction, torches, flames, or ominous glow.
- Do not add ships unless this scene requires ships, sea, harbor, fleet, sailing, or coastline.
- Do not add maps unless this scene requires maps, strategy, planning, or war table.
- Do not make every scene a battle.
- Do not make every scene about Rome.
- If the scene is about Carthage, make it Punic/Carthaginian/North African Mediterranean, not generic Roman.
- Avoid generic ancient army scenes.
- Avoid random battle scenes.
- Avoid random Roman soldiers.
- Avoid fake readable text or map labels.

SAFETY RULES:
- No gore.
- No blood.
- No injuries.
- No nudity.
- No exposed bodies.
- No graphic violence.
- All people must be fully clothed in historical clothing.
- Avoid extreme words like terror, corpse, wound, execution, naked, cutting, body, massacre, brutal, slaughter.

The final prompt must include:
- exact subject
- exact action
- location and historical period
- visible objects
- clothing and architecture
- mood and lighting
- camera/framing/composition
- vertical 9:16 composition
- dark historical graphic novel style
- Must show: ...
- Must not show: ...
- no text
- no watermark
- no modern objects

Output only the final image prompt.
"""

    final_prompt = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=IMAGE_PROMPT_MODEL,
        max_output_tokens=650,
        temperature=0.25,
        reasoning_effort="medium",
    ).strip()

    _save_cached_enhanced_prompt(prefix, scene_number, scene_hash, final_prompt)

    debug_path = os.path.join(
        TEMP_DIR,
        f"{prefix}scene_{scene_number:02d}_final_prompt.txt"
    )

    with open(debug_path, "w", encoding="utf-8") as f:
        f.write(final_prompt)

    print(f"  ✅ Scene {scene_number} prompt enhanced by GPT")
    return final_prompt


def _rewrite_prompt_after_failure(
    original_prompt: str,
    scene: dict,
    script: dict,
    prefix: str,
    attempt_number: int,
    error_message: str,
) -> str:
    scene_number = int(scene.get("scene_number", 0))
    narration_segment = scene.get("narration_segment", "")
    scene_description = scene.get("scene_description", "")
    video_title = script.get("title", "")

    stronger_safety = ""
    if attempt_number >= 3:
        stronger_safety = """
This is a repeated failure.
Use a symbolic or medium/wide composition instead:
mountains, city gates, ships, cloaks, shadows, architecture, weather, silhouettes, and atmosphere.
Keep people small, fully clothed, and non-graphic.
"""

    system_prompt = """
You rewrite failed GPT Image prompts into safer, clearer production prompts.
Output only the rewritten prompt.
No markdown.
No explanation.
"""

    user_prompt = f"""
The image provider rejected or failed this prompt.

Error:
{error_message}

Rewrite it so it keeps the same scene meaning but is safer and clearer.

Video title:
{video_title}

Scene number:
{scene_number}

Scene narration:
{narration_segment}

Scene description:
{scene_description}

Previous failed prompt:
{original_prompt}

Rules:
- Keep the same scene meaning.
- Keep the premium dark historical graphic novel style.
- Make it safe for image generation.
- Avoid words that may trigger safety filters.
- Remove or soften words like terror, dread, brutal, blood, gore, corpse, wound, execution, exposed skin, naked, cutting, body, massacre, slaughter.
- Replace extreme emotional wording with safer words like tense, afraid, anxious, serious, ominous.
- Do not include graphic violence.
- Do not include blood.
- Do not include injury.
- Do not include nudity or exposed bodies.
- All people must be fully clothed in historical clothing.
- Use fear, danger, and tension through posture, shadows, facial expression, smoke, weather, architecture, and atmosphere only.
- No text.
- No watermark.
- No modern objects.
- No readable labels.
{stronger_safety}

Output only the rewritten image prompt.
"""

    rewritten = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=IMAGE_PROMPT_MODEL,
        max_output_tokens=650,
        temperature=0.35,
        reasoning_effort="medium",
    ).strip()

    debug_path = os.path.join(
        TEMP_DIR,
        f"{prefix}scene_{scene_number:02d}_retry_{attempt_number}_prompt.txt",
    )

    with open(debug_path, "w", encoding="utf-8") as f:
        f.write(rewritten)

    return rewritten


def _build_final_prompt(prompt: str, scene_number: int) -> str:
    return f"""
{STYLE_PREFIX}

Scene {scene_number}.
{prompt}

Global image rules:
- Premium dark historical graphic novel panel.
- Stylized but believable ancient Mediterranean world.
- Cinematic ink shadows and painterly realism.
- Clear subject, readable lighting, strong silhouette.
- Serious dark-history tone, not cute or childish.
- Muted colors, deep blacks, bronze, parchment tones, and dark red accents.
- No text, no watermark, no subtitles, no logos.
- No modern objects.
- No gore, no blood, no nudity, no graphic injury.
- All people fully clothed in historical clothing.
"""


def _provider_display_name(provider: str) -> str:
    p = provider.lower().strip()
    mapping = {
        "openai_gpt_image": "OpenAI GPT Image",
        "replicate_flux_ultra": "Replicate FLUX 1.1 Pro Ultra",
        "stability_ultra": "Stability Ultra",
        "stability_sd3": "Stability SD3",
    }
    return mapping.get(p, provider)


def _replicate_secret() -> str:
    tok = str(REPLICATE_API_TOKEN or "").strip()
    return tok if tok else str(_CFG_REPLICATE_KEY or "").strip()


def _replicate_create_prediction(
    create_url: str,
    payload: dict,
    headers: dict,
    *,
    max_rate_retries: int = 4,
) -> dict:
    last_response_text = ""

    for rate_try in range(1, max_rate_retries + 1):
        create = requests.post(create_url, json=payload, headers=headers, timeout=120)

        if create.status_code in (200, 201):
            return create.json()

        last_response_text = create.text

        if create.status_code == 429:
            retry_after = 12

            try:
                data = create.json()
                retry_after = int(data.get("retry_after", retry_after))
            except Exception:
                pass

            retry_after = max(retry_after, 12)
            print(
                f"  ⏳ Replicate rate limit hit "
                f"({rate_try}/{max_rate_retries}). Waiting {retry_after}s..."
            )
            time.sleep(retry_after)
            continue

        raise Exception(
            f"Replicate create error {create.status_code}: {create.text}"
        )

    raise Exception(f"Replicate create error 429 after retries: {last_response_text}")


def _generate_replicate_flux_ultra(
    final_prompt: str,
    aspect_ratio: str,
    *,
    raw_mode: bool = True,
    safety_tolerance: int = 5,
) -> bytes:
    token = _replicate_secret()
    if not token:
        raise Exception(
            "Set REPLICATE_API_TOKEN or REPLICATE_API_KEY for Replicate image generation."
        )

    model = REPLICATE_FLUX_MODEL.strip("/")
    create_url = f"https://api.replicate.com/v1/models/{model}/predictions"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "input": {
            "prompt": final_prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": "jpg",
            "raw": bool(raw_mode),
            "safety_tolerance": int(safety_tolerance),
        }
    }

    prediction = _replicate_create_prediction(create_url, payload, headers)
    poll_url = (prediction.get("urls") or {}).get("get")

    if not poll_url:
        raise Exception("Replicate missing poll URL in response")

    deadline = time.time() + 900
    headers_poll = {"Authorization": f"Bearer {token}"}

    while time.time() < deadline:
        poll = requests.get(poll_url, headers=headers_poll, timeout=120)

        if poll.status_code == 429:
            print("  ⏳ Replicate poll rate limit hit. Waiting 12s...")
            time.sleep(12)
            continue

        if poll.status_code != 200:
            raise Exception(f"Replicate poll error {poll.status_code}: {poll.text}")

        pred = poll.json()
        status = pred.get("status")

        if status == "succeeded":
            output = pred.get("output")
            img_url = None

            if isinstance(output, list) and output:
                img_url = output[0]
            elif isinstance(output, str):
                img_url = output

            if not img_url:
                raise Exception("Replicate prediction succeeded but output was empty.")

            dl = requests.get(img_url, timeout=300)
            dl.raise_for_status()
            return dl.content

        if status in ("failed", "canceled", "cancelled"):
            detail = pred.get("error") or pred
            raise Exception(f"Replicate prediction {status}: {detail}")

        time.sleep(2)

    raise Exception("Replicate prediction timed out while polling.")


def _generate_openai_gpt_image(final_prompt: str, aspect_ratio: str) -> bytes:
    if not str(OPENAI_API_KEY or "").strip():
        raise Exception("Set OPENAI_API_KEY for OpenAI image generation.")

    if aspect_ratio == "16:9":
        size = "1536x1024"
    else:
        size = OPENAI_IMAGE_SIZE or "1024x1536"

    payload = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": final_prompt,
        "size": size,
        "quality": OPENAI_IMAGE_QUALITY,
        "moderation": OPENAI_IMAGE_MODERATION,
        "output_format": OPENAI_IMAGE_OUTPUT_FORMAT,
        "n": 1,
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        OPENAI_IMAGE_GENERATION_URL,
        headers=headers,
        json=payload,
        timeout=300,
    )

    if response.status_code != 200:
        raise Exception(
            f"OpenAI image error {response.status_code}: {response.text[:1000]}"
        )

    data = response.json()
    items = data.get("data") or []

    if not items:
        raise Exception(f"OpenAI image response had no data: {data}")

    first = items[0]
    b64 = first.get("b64_json")

    if b64:
        return base64.b64decode(b64)

    url = first.get("url")
    if url:
        dl = requests.get(url, timeout=300)
        dl.raise_for_status()
        return dl.content

    raise Exception(f"OpenAI image response missing b64_json/url: {data}")


def _generate_stability_image(final_prompt: str, aspect_ratio: str, *, use_ultra: bool) -> bytes:
    provider = "stability_ultra" if use_ultra else "stability_sd3"
    url = STABLE_IMAGE_ULTRA_URL if use_ultra else SD3_GENERATE_URL

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {STABILITY_API_KEY}",
            "Accept": "image/*",
        },
        files={
            "prompt": (None, final_prompt),
            "negative_prompt": (None, NEGATIVE_PROMPT),
            "output_format": (None, "jpeg"),
            "aspect_ratio": (None, aspect_ratio),
        },
        timeout=300,
    )

    if response.status_code != 200:
        raise Exception(
            f"Stability {provider} error {response.status_code}: {response.text}"
        )

    return response.content


def _generate_image_bytes(
    final_prompt: str,
    aspect_ratio: str,
    *,
    raw_mode: bool | None = None,
    safety_tolerance: int | None = None,
) -> bytes:
    provider = str(IMAGE_GENERATION_PROVIDER).lower().strip()

    if provider == "openai_gpt_image":
        return _generate_openai_gpt_image(final_prompt, aspect_ratio)

    if provider == "replicate_flux_ultra":
        raw = REPLICATE_FLUX_RAW if raw_mode is None else raw_mode
        safety = _safe_int(
            REPLICATE_SAFETY_TOLERANCE if safety_tolerance is None else safety_tolerance,
            5,
        )

        return _generate_replicate_flux_ultra(
            final_prompt,
            aspect_ratio,
            raw_mode=bool(raw),
            safety_tolerance=safety,
        )

    if provider == "stability_ultra":
        return _generate_stability_image(final_prompt, aspect_ratio, use_ultra=True)

    if provider == "stability_sd3":
        return _generate_stability_image(final_prompt, aspect_ratio, use_ultra=False)

    raise Exception(
        f"Unknown IMAGE_GENERATION_PROVIDER: {IMAGE_GENERATION_PROVIDER}. "
        "Use 'openai_gpt_image', 'replicate_flux_ultra', 'stability_ultra', or 'stability_sd3'."
    )


def _generate_fallback_stability(final_prompt: str, aspect_ratio: str) -> bytes:
    print("  🛟 Falling back to Stability Ultra for this scene...")
    return _generate_stability_image(final_prompt, aspect_ratio, use_ultra=True)


def _attempt_settings(attempt: int, previous_error: Exception | None) -> tuple[bool, int]:
    base_safety = max(5, _safe_int(REPLICATE_SAFETY_TOLERANCE, 5))
    base_raw = bool(REPLICATE_FLUX_RAW)

    if attempt == 1:
        return base_raw, base_safety

    if attempt == 2:
        return base_raw, min(6, max(base_safety, 6))

    if attempt == 3:
        if previous_error is not None and _is_safety_error(previous_error):
            return False, 6
        return base_raw, 6

    return False, 6


def _is_billing_or_quota_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return any(
        phrase in text
        for phrase in [
            "billing_hard_limit_reached",
            "insufficient_quota",
            "quota",
            "429",
            "insufficient funds",
            "balance",
            "billing",
            "limit reached",
        ]
    )


def generate_image(
    scene_or_prompt,
    scene_number: int,
    prefix: str,
    script: dict | None = None,
    *,
    force: bool = False,
) -> str:
    os.makedirs(TEMP_DIR, exist_ok=True)

    output_path = os.path.join(
        TEMP_DIR,
        f"{prefix}scene_{scene_number:02d}.jpg"
    )

    # REUSE-FIRST: check if valid image exists
    if not force and os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
        print(f"  ⚡ Scene {scene_number} image reused from cache.")
        return output_path

    scene_hash = None

    if isinstance(scene_or_prompt, dict) and script is not None:
        scene = scene_or_prompt
        production_prompt = enhance_image_prompt(scene, script, prefix)
        scene_hash = _hash_scene(scene, script)
    else:
        scene = {
            "scene_number": scene_number,
            "narration_segment": "",
            "scene_description": "",
            "image_prompt": str(scene_or_prompt),
        }
        script = script or {"title": "", "full_narration": ""}
        production_prompt = str(scene_or_prompt)

    last_error = None

    for attempt in range(1, MAX_IMAGE_GENERATION_ATTEMPTS + 1):
        raw_mode, safety_tolerance = _attempt_settings(attempt, last_error)

        try:
            final_prompt = _build_final_prompt(production_prompt, scene_number)

            provider = str(IMAGE_GENERATION_PROVIDER).lower().strip()

            if provider == "replicate_flux_ultra":
                print(
                    f"  🎛️ Scene {scene_number} attempt {attempt}: "
                    f"raw={raw_mode}, safety_tolerance={safety_tolerance}"
                )

            if provider == "openai_gpt_image":
                print(
                    f"  🎛️ Scene {scene_number} attempt {attempt}: "
                    f"OpenAI model={OPENAI_IMAGE_MODEL}, "
                    f"size={OPENAI_IMAGE_SIZE}, quality={OPENAI_IMAGE_QUALITY}, "
                    f"moderation={OPENAI_IMAGE_MODERATION}"
                )

            image_bytes = _generate_image_bytes(
                final_prompt,
                aspect_ratio="9:16",
                raw_mode=raw_mode,
                safety_tolerance=safety_tolerance,
            )

            with open(output_path, "wb") as f:
                f.write(image_bytes)

            prov = _provider_display_name(str(IMAGE_GENERATION_PROVIDER))

            if attempt == 1:
                print(f"  🖼️  Scene {scene_number} image generated — provider: {prov}")
            else:
                print(
                    f"  🖼️  Scene {scene_number} image generated on retry {attempt} "
                    f"— provider: {prov}"
                )

            return output_path

        except Exception as e:
            last_error = e
            print(
                f"  ⚠️ Scene {scene_number} image attempt "
                f"{attempt}/{MAX_IMAGE_GENERATION_ATTEMPTS} failed: {e}"
            )

            # COST PROTECTION: Stop immediately on billing/quota errors
            if _is_billing_or_quota_error(e):
                print(f"  🛑 Stopping image generation due to billing/quota error: {e}")
                raise

            if attempt >= MAX_IMAGE_GENERATION_ATTEMPTS:
                break

            production_prompt = _rewrite_prompt_after_failure(
                original_prompt=production_prompt,
                scene=scene,
                script=script,
                prefix=prefix,
                attempt_number=attempt + 1,
                error_message=str(e),
            )

            if scene_hash is not None:
                _save_retry_prompt_to_cache(
                    prefix=prefix,
                    scene_number=scene_number,
                    scene_hash=scene_hash,
                    prompt=production_prompt,
                )

            time.sleep(1.0)

    try:
        final_prompt = _build_final_prompt(production_prompt, scene_number)
        image_bytes = _generate_fallback_stability(final_prompt, aspect_ratio="9:16")

        with open(output_path, "wb") as f:
            f.write(image_bytes)

        print(f"  🖼️  Scene {scene_number} image generated with fallback Stability Ultra")
        return output_path

    except Exception as fallback_error:
        if _is_billing_or_quota_error(fallback_error):
             print(f"  🛑 Stopping image generation due to billing/quota error (fallback): {fallback_error}")
             raise
        raise Exception(
            f"Scene {scene_number} image generation failed after "
            f"{MAX_IMAGE_GENERATION_ATTEMPTS} attempts and Stability fallback. "
            f"Last primary error: {last_error}. "
            f"Fallback error: {fallback_error}"
        )


def generate_all_images(scenes: list, prefix: str, *, force: bool = False) -> list:
    image_paths = []

    pseudo_script = {
        "title": "",
        "full_narration": " ".join(
            str(s.get("narration_segment", "")) for s in scenes
        ),
        "scenes": scenes,
    }

    print(f"\n🎨 Generating {len(scenes)} images...")

    for scene in scenes:
        scene_num = int(scene["scene_number"])

        path = generate_image(scene, scene_num, prefix, pseudo_script, force=force)

        image_paths.append({
            "scene_number": scene_num,
            "path": path,
            "timestamp_start": scene["timestamp_start"],
            "timestamp_end": scene["timestamp_end"],
        })

        time.sleep(0.5)

    print(f"✅ All images ready: {len(image_paths)} scenes")
    return image_paths


def generate_thumbnail_image(prompt: str, prefix: str) -> str:
    os.makedirs(TEMP_DIR, exist_ok=True)

    final_prompt = f"""
{STYLE_PREFIX}

{prompt}

Thumbnail rules:
- Premium dark historical graphic novel poster.
- One strong focal subject.
- High contrast, readable subject.
- Dramatic but not underexposed.
- Works as a 1280x720 YouTube thumbnail.
- No text, no captions, no watermark, no logo.
- No modern objects.
- No gore, no blood, no nudity.
"""

    image_bytes = _generate_image_bytes(
        final_prompt,
        aspect_ratio="16:9",
        raw_mode=bool(REPLICATE_FLUX_RAW),
        safety_tolerance=max(5, _safe_int(REPLICATE_SAFETY_TOLERANCE, 5)),
    )

    output_path = os.path.join(TEMP_DIR, f"{prefix}thumbnail.jpg")

    with open(output_path, "wb") as f:
        f.write(image_bytes)

    prov = _provider_display_name(str(IMAGE_GENERATION_PROVIDER))
    print(f"✅ Thumbnail generated — provider: {prov} → {output_path}")
    return output_path