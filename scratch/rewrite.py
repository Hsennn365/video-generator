import os

with open("modules/script.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# find index of: "def _generate_episode_script_from_timeline("
start_idx = 0
for i, line in enumerate(lines):
    if line.startswith("def _generate_episode_script_from_timeline("):
        start_idx = i
        break

new_code = """def _generate_episode_script_from_timeline(
    *,
    episode_plan: dict,
    series_name: str,
    total_parts: int,
    master_timeline: list[dict],
    timeline_events: list[dict],
    previous_script: dict | None,
    next_topic: dict | None,
    research_context: str = "",
) -> dict:
    part_num = int(episode_plan.get("episode_number", 1))
    
    # We construct a fake topic dict to reuse _series_cta
    fake_topic = {"series": series_name, "part": part_num}
    if total_parts:
        fake_topic["episode_count"] = total_parts
    cta = _series_cta(fake_topic)

    previous_context = (
        json.dumps(_compact_script_for_context(previous_script), indent=2, ensure_ascii=False)
        if previous_script else "null"
    )
    next_context = json.dumps(next_topic, indent=2, ensure_ascii=False) if next_topic else "null"
    timeline_json = json.dumps(master_timeline, indent=2, ensure_ascii=False)
    assigned_json = json.dumps(timeline_events, indent=2, ensure_ascii=False)
    plan_json = json.dumps(episode_plan, indent=2, ensure_ascii=False)

    system_prompt = _base_system_prompt() + f\"\"\"

EPISODE-FROM-MASTER-TIMELINE MODE:
You are now writing exactly ONE episode from a prebuilt chronological master timeline and episode plan.

The master timeline and episode plan are the sources of truth.
You must follow the episode plan precisely.

EPISODE RULES:
- Write exactly {TARGET_SCENES} scenes.
- The episode should be about {TARGET_WORDS_LOW}-{TARGET_WORDS_HIGH} spoken words.
- Scene 1 must start with: "Part {part_num}."
- Final scene must end with exactly: "{cta}"
- CTA must appear once only, inside the final scene.
- No scene can be CTA-only.
- No duplicate narration segments.
- No repeated filler beats.
- Every scene must advance the episode.

MASTER TIMELINE RULES:
- Use ONLY the assigned timeline events for this episode.
- Mention each assigned event clearly in order.
- If the previous episode left an unresolved action, resolve it physically and causally in scenes 1-3.
- The final two scenes must point naturally toward the next topic.
\"\"\"

    user_prompt = f\"\"\"
Series name:
{series_name}

Total known parts:
{total_parts}

Current episode plan:
{plan_json}

Previous episode context:
{previous_context}

Next episode context:
{next_context}

Full master timeline for context:
{timeline_json}

Assigned timeline events for THIS episode:
{assigned_json}

Verified research context:
{research_context if research_context else "No research context provided."}

Return ONLY this exact JSON script shape:

{{
  "title": "Compelling title under 60 characters",
  "hook": "The exact opening hook, short and suspenseful, no markers",
  "full_narration": "Full narration made from all narration_segment values in order, with optional performance markers between sentences only.",
  "scenes": [
    {{
      "scene_number": 1,
      "timestamp_start": 0,
      "timestamp_end": 6,
      "narration_segment": "Exact spoken narration for this one visual beat only, no markers",
      "image_prompt": "Scene-locked image prompt matching only this narration segment",
      "scene_description": "One sentence describing the visual"
    }}
  ],
  "hashtags": ["ten", "short", "relevant", "keywords", "without", "hash", "symbols", "about", "the", "topic"],
  "youtube_description": "A short natural description, around 90–130 words.",
  "cta": "{cta}"
}}
\"\"\"

    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=6500,
        temperature=0.62,
    )

    return _extract_json(raw)


def generate_series_script_package(
    topic: dict,
    *,
    overwrite_all: bool = False,
    rebuild_research: bool = False,
) -> dict:
    if not _is_series_topic(topic):
        return generate_script(topic)

    series_name = str(topic.get("title", "") or "").strip()
    current_topic_id = str(topic.get("id"))
    total_parts = _series_total_parts(topic)

    if not total_parts or total_parts <= 0:
        raise Exception(f"Series topic {current_topic_id} is missing a valid episode_count.")

    # -------------------------------------------------------
    # STAGE 0: Ensure research story bible exists.
    # -------------------------------------------------------
    research = ensure_series_research(
        topic, rebuild=rebuild_research
    )
    research_context = _format_research_context(research)
    research_hash = get_research_hash(research)
    if research_hash:
        print(f"   📚 Series research loaded (hash: {research_hash})")
    else:
        print("   ⚠️  No research context available — proceeding without story bible.")

    # Find locked episodes
    locked_previous_scripts: list[dict] = []
    target_episodes: list[int] = []

    for ep in range(1, total_parts + 1):
        if not overwrite_all:
            existing = _load_json_file(_topic_script_path(topic, episode_num=ep), default=None)
            if existing:
                locked_previous_scripts.append(_compact_script_for_context(existing))
                continue
        target_episodes.append(ep)

    if not target_episodes:
        existing_current = _load_json_file(_topic_script_path(topic, episode_num=1), default=None)
        if existing_current:
            return existing_current
        raise Exception("No target topics to generate and current script cache is missing.")

    locked_context = json.dumps(locked_previous_scripts, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------
    # STAGE 1: Generate Master Timeline and Episode Plans
    # ------------------------------------------------------------
    system_prompt = _series_master_system_prompt() + \"\"\"

MASTER TIMELINE AND EPISODE PLANS:
For this call, return the master chronological story spine AND the specific episode plans.
Do not write full episode scripts in this response.

Return ONLY this JSON structure:

{{
  "series": "Series Name",
  "master_timeline": [
    {{
      "event_id": "E01",
      "event": "Specific chronological event",
      "why_it_matters": "Short causal explanation"
    }}
  ],
  "episode_plans": [
    {{
      "episode_number": 1,
      "episode_title": "Title",
      "episode_summary": "Summary",
      "timeline_event_ids": ["E01", "E02"],
      "opening_hook_angle": "Angle",
      "ending_bridge": "Bridge",
      "key_people_to_introduce": ["Person 1"],
      "key_places_to_anchor": ["Place 1"],
      "must_not_skip": "Critical fact"
    }}
  ]
}}
\"\"\"

    user_prompt = f\"\"\"
Series name:
{series_name}

Total known parts:
{total_parts}

Series Description:
{topic.get("description", "")}

Locked previous approved scripts:
{locked_context}

Verified research context:
{research_context if research_context else "No research context provided."}

Create a master timeline AND episode plans for the missing target episodes: {target_episodes}.

Rules:
- Continue from locked previous scripts if they exist.
- Do not rewrite locked previous episodes.
- Assign every event to an episode.
- The final event of each episode must naturally set up the next episode.
\"\"\"

    raw = _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=8500,
        temperature=0.55,
    )

    payload = _extract_json(raw)
    master_timeline = payload.get("master_timeline", [])
    episode_plans = payload.get("episode_plans", [])
    
    if not isinstance(master_timeline, list) or not master_timeline:
        raise Exception(f"Series master timeline returned no events: {payload}")
    if not isinstance(episode_plans, list) or not episode_plans:
        raise Exception(f"Series master timeline returned no episode_plans: {payload}")

    master_path = get_series_package_path(topic)
    if research_hash:
        payload["research_hash"] = research_hash
    _save_json_file(master_path, payload)
    print(f"   🧭 Saved series package (timeline + plans): {master_path}")

    # ------------------------------------------------------------
    # STAGE 2: Generate each target episode separately
    # ------------------------------------------------------------
    saved_by_id: dict[int, dict] = {}

    previous_full_script_by_part: dict[int, dict] = {}
    for locked in locked_previous_scripts:
        locked_part = int(locked.get("part", 0) or 0)
        if locked_part > 0:
            full_locked = _load_json_file(_topic_script_path(topic, episode_num=locked_part), default=None)
            if full_locked:
                previous_full_script_by_part[locked_part] = full_locked

    for episode_plan in sorted(episode_plans, key=lambda x: int(x.get("episode_number", 0))):
        episode_part = int(episode_plan.get("episode_number", 0))
        if episode_part not in target_episodes:
            continue

        assigned_event_ids = episode_plan.get("timeline_event_ids", [])
        timeline_events = [e for e in master_timeline if e.get("event_id") in assigned_event_ids]

        if not timeline_events:
            timeline_events = [
                {
                    "event_id": f"P{episode_part}_FALLBACK",
                    "event": str(episode_plan.get("episode_summary", "")),
                    "why_it_matters": "This topic must still be produced.",
                }
            ]

        previous_script = previous_full_script_by_part.get(episode_part - 1)
        next_topic = None # Not strictly used now, but we can leave it None

        print(
            f"   📝 Generating episode script from timeline: "
            f"topic {current_topic_id}, part {episode_part}, events={{len(timeline_events)}}"
        )

        script_obj = _generate_episode_script_from_timeline(
            episode_plan=episode_plan,
            series_name=series_name,
            total_parts=total_parts,
            master_timeline=master_timeline,
            timeline_events=timeline_events,
            previous_script=previous_script,
            next_topic=next_topic,
            research_context=research_context,
        )

        # We construct a fake topic for postprocessing
        fake_topic = topic.copy()
        fake_topic["part"] = episode_part

        script_obj = _postprocess_validate_repair(
            script_obj,
            fake_topic,
            previous_script=previous_script,
            next_topic=next_topic,
            research_context=research_context,
        )

        script_obj["topic_id"] = current_topic_id
        script_obj["series"] = series_name
        script_obj["part"] = episode_part
        script_obj["timeline_event_ids"] = assigned_event_ids
        if research_hash:
            script_obj["research_hash"] = research_hash

        path = _topic_script_path(topic, episode_num=episode_part)
        _save_json_file(path, script_obj)
        saved_by_id[episode_part] = script_obj
        previous_full_script_by_part[episode_part] = script_obj
        print(f"   ✅ Saved connected script: {path}")

    # Return the first target episode if available
    first_target = target_episodes[0] if target_episodes else 1
    selected = saved_by_id.get(first_target)
    if selected:
        print(f"✅ Connected series package generated for {series_name}")
        return selected

    existing_current = _load_json_file(_topic_script_path(topic, episode_num=1), default=None)
    if existing_current:
        return existing_current

    raise Exception(f"Series package did not produce any scripts for {series_name}")


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower())
    return value.strip("_") or "series"


def generate_thumbnail_prompt(script: dict) -> str:
    \"\"\"Generate a specific prompt for the thumbnail image.\"\"\"
    system_prompt = (
        "You write premium GPT Image prompts for dark cinematic history thumbnails. "
        "Output only the prompt."
    )

    user_prompt = f\"\"\"
Create a thumbnail image prompt for this dark cinematic history video.

Title:
{script['title']}

Hook:
{script['hook']}

The thumbnail should:
- Look like a dark historical movie poster / graphic novel poster
- Have one clear focal subject
- Be readable instantly
- Communicate the topic within one second
- Use dramatic but visible lighting
- Use smoke, firelight, gold, shadows, or blood-red tones only if appropriate
- Work as a 1280x720 YouTube thumbnail
- Be cinematic, stylized, and premium
- Contain no text
- Contain no watermark
- Contain no logos
- Contain no modern objects

Return ONLY the image generation prompt, nothing else.
\"\"\"

    return _openai_text_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=SCRIPT_MODEL,
        max_output_tokens=350,
        temperature=0.55,
    ).strip()
"""

# Append the new code
with open("modules/script.py", "w", encoding="utf-8") as f:
    f.writelines(lines[:start_idx])
    f.write(new_code)
