import re

with open('modules/script.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('_topic_part(', '_get_episode_num(')

with open('modules/script.py', 'w', encoding='utf-8') as f:
    f.write(content)
