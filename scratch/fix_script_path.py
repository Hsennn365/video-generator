import sys

with open("modules/script.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace definition
old_def = """def _topic_script_path(topic: dict, episode_num: int | None = None) -> str:
    topic_id = str(topic.get("id", "0")).strip()
    if _is_series_topic(topic):
        ep = int(episode_num) if episode_num else 1
        from config import SERIES_DATA_DIR
        return os.path.join(SERIES_DATA_DIR, f"series_{topic_id}", "episodes", f"episode_{ep:02d}_script.json")
    return os.path.join(OUTPUT_DIR, f"topic_{topic_id}_script.json")"""

new_def = """def get_script_path(topic: dict, episode_num: int | None = None) -> str:
    topic_id = str(topic.get("id", "0")).strip()
    
    try:
        from config import SERIES_DATA_DIR
    except ImportError:
        SERIES_DATA_DIR = "series_data"
        
    try:
        from config import OUTPUT_DIR
    except ImportError:
        OUTPUT_DIR = "output"

    if _is_series_topic(topic):
        if episode_num is None:
            raise ValueError(f"episode_num is required for series topics. Topic ID: {topic_id}")
        ep = int(episode_num)
        return os.path.join(SERIES_DATA_DIR, f"series_{topic_id}", "episodes", f"episode_{ep:02d}_script.json")
    
    return os.path.join(OUTPUT_DIR, f"topic_{topic_id}_script.json")"""

content = content.replace(old_def, new_def)

# Replace usages
content = content.replace("_topic_script_path", "get_script_path")

with open("modules/script.py", "w", encoding="utf-8") as f:
    f.write(content)
