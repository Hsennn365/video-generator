import sys

with open("main.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

content = "".join(lines)

# 1. Imports
content = content.replace(
    "generate_thumbnail_prompt,",
    "generate_thumbnail_prompt,\n    get_asset_prefix,\n    get_script_path,\n    _is_series_topic,"
)

# 2. Add argparse
content = content.replace(
    "parser.add_argument(\"--topic-id\"",
    "parser.add_argument(\"--build-series\", action=\"store_true\", help=\"Generate all episode scripts for a series\")\n    parser.add_argument(\"--episode\", type=int, help=\"Episode number to process for a series\")\n    parser.add_argument(\"--topic-id\""
)

# 3. Pass kwargs in main()
content = content.replace(
    "post_only=args.post,",
    "post_only=args.post,\n        build_series=args.build_series,\n        episode_num=args.episode,"
)

# 4. run_pipeline signature
content = content.replace(
    "post_only: bool = False,",
    "post_only: bool = False,\n    build_series: bool = False,\n    episode_num: int | None = None,"
)

# 5. run_pipeline start logic
orig_start = """    print(f"\\n{'=' * 60}")
    print(f"🎬 TOPIC: {topic['title']}")
    if topic.get("series") and topic.get("part", "0") != "0":
        print(f"   Series: {topic['series']} — Part {topic['part']}")
    print(f"{'=' * 60}\\n")

    start_time = datetime.now()
    topic_id = str(topic["id"])

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    script_path = os.path.join(OUTPUT_DIR, f"topic_{topic_id}_script.json")
    audio_path = os.path.join(TEMP_DIR, f"topic_{topic_id}_voiceover.mp3")
    captions_path = os.path.join(TEMP_DIR, f"topic_{topic_id}_captions.json")
    thumbnail_path = os.path.join(TEMP_DIR, f"topic_{topic_id}_thumbnail.jpg")
    meta_path = os.path.join(TEMP_DIR, f"topic_{topic_id}_cache_meta.json")"""

new_start = """    print(f"\\n{'=' * 60}")
    print(f"🎬 TOPIC: {topic['title']}")
    if _is_series_topic(topic):
        print(f"   Series mode enabled")
        if episode_num:
            print(f"   Processing Episode {episode_num}")
    print(f"{'=' * 60}\\n")

    start_time = datetime.now()
    topic_id = str(topic["id"])

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    # Series Validation
    if _is_series_topic(topic):
        if rebuild_script:
            print("⚠️  --rebuild-script is blocked for series. Use --build-series to ensure continuity.")
            return

        if not build_series and not episode_num:
            print("⚠️  For series, you must specify either --build-series (to write scripts) or --episode X (to render video).")
            return

    script_path = get_script_path(topic, episode_num)
    prefix = get_asset_prefix(topic, episode_num)
    
    audio_path = os.path.join(TEMP_DIR, f"{prefix}voiceover.mp3")
    captions_path = os.path.join(TEMP_DIR, f"{prefix}captions.json")
    thumbnail_path = os.path.join(TEMP_DIR, f"{prefix}thumbnail.jpg")
    meta_path = os.path.join(TEMP_DIR, f"{prefix}cache_meta.json")"""
content = content.replace(orig_start, new_start)

# 6. Step 1 (Script Generation logic)
orig_step1 = """        if os.path.isfile(script_path) and not rebuild_script and not rebuild_series_scripts:
            print("⚡ Script loaded from cache")
            script = _load_json(script_path)
            if not script:
                raise Exception(f"Failed to load existing script from {script_path}")
        else:
            print("\\n✍️  STEP 1/6: Generating script...")
            if rebuild_series_scripts:
                print("   Rebuilding connected series scripts...")
                script = generate_series_script_package(topic, overwrite_all=True, rebuild_research=rebuild_research)
            else:
                script = generate_series_script_package(topic, rebuild_research=rebuild_research)"""

new_step1 = """        if _is_series_topic(topic):
            if build_series:
                print("\\n✍️  STEP 1/6: Building series scripts...")
                script = generate_series_script_package(topic, overwrite_all=rebuild_series_scripts, rebuild_research=rebuild_research)
                if not episode_num:
                    print("\\n✅ Series build complete. Exiting because --episode was not specified.")
                    return
            
            # If we are here, episode_num is specified
            if os.path.isfile(script_path):
                print("⚡ Script loaded from cache")
                script = _load_json(script_path)
                if not script:
                    raise Exception(f"Failed to load existing script from {script_path}")
            else:
                print(f"\\n❌ Episode script not found. Run python main.py --topic-id {topic_id} --build-series first.")
                return
        else:
            if os.path.isfile(script_path) and not rebuild_script:
                print("⚡ Script loaded from cache")
                script = _load_json(script_path)
                if not script:
                    raise Exception(f"Failed to load existing script from {script_path}")
            else:
                print("\\n✍️  STEP 1/6: Generating script...")
                script = generate_script(topic)"""
content = content.replace(orig_step1, new_step1)

# 7. Replace old hardcoded prefixes in the rest of main.py
content = content.replace('f"topic_{topic_id}_voiceover.mp3"', 'f"{prefix}voiceover.mp3"')
content = content.replace('f"topic_{topic_id}_scene_{_scene_num:02d}.jpg"', 'f"{prefix}scene_{_scene_num:02d}.jpg"')
content = content.replace('f"topic_{topic_id}_scene_{scene_num:02d}.jpg"', 'f"{prefix}scene_{scene_num:02d}.jpg"')

# In post-only
content = content.replace('f"topic_{topic_id}_*.mp4"', 'f"{prefix}*.mp4"')

with open("main.py", "w", encoding="utf-8") as f:
    f.write(content)
