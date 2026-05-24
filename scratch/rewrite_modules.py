import sys

def replace_in_file(path, replacements):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for old, new in replacements:
        content = content.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# 1. captions.py
replace_in_file("modules/captions.py", [
    ("def generate_captions(audio_path: str, topic_id: str) -> list:", "def generate_captions(audio_path: str, prefix: str) -> list:"),
    ('os.path.join(TEMP_DIR, f"topic_{topic_id}_captions.json")', 'os.path.join(TEMP_DIR, f"{prefix}captions.json")')
])

# 2. assembly.py
replace_in_file("modules/assembly.py", [
    ("topic_id: str,", "prefix: str,"),
    ('f"local_render_topic_{topic_id}"', 'f"local_render_{prefix.strip(\'_\')}"'),
    ('f"topic_{topic_id}_captions.ass"', 'f"{prefix}captions.ass"'),
    ('f"topic_{topic_id}"', 'f"{prefix.strip(\'_\')}"'),
    ('f"topic_{topic_id}_{safe_title}_{timestamp}.mp4"', 'f"{prefix}{safe_title}_{timestamp}.mp4"')
])

# 3. images.py
replace_in_file("modules/images.py", [
    ("topic_id: str", "prefix: str"),
    ("topic_id=topic_id", "prefix=prefix"),
    ('f"topic_{topic_id}_enhanced_prompts.json"', 'f"{prefix}enhanced_prompts.json"'),
    ('f"topic_{topic_id}_scene_{scene_number:02d}_final_prompt.txt"', 'f"{prefix}scene_{scene_number:02d}_final_prompt.txt"'),
    ('f"topic_{topic_id}_visual_prompt_plan.json"', 'f"{prefix}visual_prompt_plan.json"'),
    ('f"topic_{topic_id}_scene_{scene_number:02d}_retry_{attempt_number}_prompt.txt"', 'f"{prefix}scene_{scene_number:02d}_retry_{attempt_number}_prompt.txt"'),
    ('f"topic_{topic_id}_scene_{scene_number:02d}.jpg"', 'f"{prefix}scene_{scene_number:02d}.jpg"'),
    ('f"topic_{topic_id}_thumbnail.jpg"', 'f"{prefix}thumbnail.jpg"')
])

# 4. main.py - Need to fix the calls!
with open("main.py", "r", encoding="utf-8") as f:
    main_content = f.read()

main_content = main_content.replace('topic_id=topic_id,', 'prefix=prefix,')
main_content = main_content.replace('generate_captions(audio_path, topic_id)', 'generate_captions(audio_path, prefix)')
main_content = main_content.replace('generate_image(scene, scene_num, topic_id, script)', 'generate_image(scene, scene_num, prefix, script)')
main_content = main_content.replace('prepare_scene_image_prompts(script, topic_id, force=rebuild_images)', 'prepare_scene_image_prompts(script, prefix, force=rebuild_images)')
main_content = main_content.replace('generate_thumbnail_image(thumbnail_prompt, topic_id)', 'generate_thumbnail_image(thumbnail_prompt, prefix)')

with open("main.py", "w", encoding="utf-8") as f:
    f.write(main_content)
