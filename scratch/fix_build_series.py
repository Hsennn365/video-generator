import sys

with open("main.py", "r", encoding="utf-8") as f:
    content = f.read()

old_validation = """    # Series Validation
    if _is_series_topic(topic):
        if rebuild_script:
            print("⚠️  --rebuild-script is blocked for series. Use --build-series to ensure continuity.")
            return

        if not build_series and not episode_num:
            print("⚠️  For series, you must specify either --build-series (to write scripts) or --episode X (to render video).")
            return

    script_path = get_script_path(topic, episode_num)
    prefix = get_asset_prefix(topic, episode_num)"""

new_validation = """    # Series Validation
    if _is_series_topic(topic):
        if rebuild_script:
            print("⚠️  --rebuild-script is blocked for series. Use --build-series to ensure continuity.")
            return

        if build_series:
            print("\\n✍️  Building series script package...")
            from modules.script import generate_series_script_package
            # Need to determine overwrite_all behavior. Using rebuild_series_scripts if set, else False
            generate_series_script_package(topic, overwrite_all=rebuild_series_scripts, rebuild_research=rebuild_research)
            print("\\n✅ Series build complete.")
            return

        if not episode_num:
            print("⚠️  For series, you must specify either --build-series (to write scripts) or --episode X (to render video).")
            return

    script_path = get_script_path(topic, episode_num)
    prefix = get_asset_prefix(topic, episode_num)"""

content = content.replace(old_validation, new_validation)

old_step1_series = """        if _is_series_topic(topic):
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
                return"""

new_step1_series = """        if _is_series_topic(topic):
            if os.path.isfile(script_path):
                print("⚡ Script loaded from cache")
                script = _load_json(script_path)
                if not script:
                    raise Exception(f"Failed to load existing script from {script_path}")
            else:
                print(f"\\n❌ Episode script not found. Run python main.py --topic-id {topic_id} --build-series first.")
                return"""

content = content.replace(old_step1_series, new_step1_series)

with open("main.py", "w", encoding="utf-8") as f:
    f.write(content)
