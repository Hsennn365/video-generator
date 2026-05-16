# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"Ash & Empires" — a Python pipeline that generates 90-second dark-history short videos and posts them to YouTube Shorts, TikTok, and Instagram Reels. There is no build system, no test suite, and no formal package layout; it is a single-script orchestrator (`main.py`) that calls into `modules/` and reads everything from `config.py` and `topics.csv`.

## Common commands

```bash
pip install -r requirements.txt        # install deps (note: requirements.txt is minimal — see "Dependencies" below)

python main.py                         # process next pending topic in topics.csv
python main.py --topic-id 3            # process a specific topic
python main.py --preview               # generate but don't post
python main.py --post --topic-id 3     # post the latest local render for a topic
python main.py --yes                   # auto-confirm the post prompt (for cron/Task Scheduler)
python main.py --list                  # list all topics + status
python main.py --voices                # list ElevenLabs voices
python main.py --analytics             # update + print performance data (run ~48h after posting)
python main.py --summary               # print performance summary
```

### Selective rebuilds (the main dev/debug workflow)

The pipeline is heavily cached under `temp/` and `output/`. When iterating, use the narrowest `--rebuild-*` flag, not `--rebuild-all`:

```bash
python main.py --topic-id 1 --rebuild-assembly                          # re-render only (cheapest)
python main.py --topic-id 1 --rebuild-captions                          # regenerate captions from existing audio
python main.py --topic-id 1 --rebuild-audio                             # regenerate voiceover (auto cascades to captions + assembly)
python main.py --topic-id 1 --rebuild-images                            # regenerate scene images
python main.py --topic-id 1 --rebuild-script                            # regenerate script (auto cascades to ALL downstream assets)
python main.py --topic-id 1 --rebuild-series-scripts                    # regenerate the full connected series for this topic
python main.py --topic-id 1 --rebuild-all                               # nuclear option
```

Rebuild flags **cascade automatically** in `run_pipeline()`: rebuilding script invalidates audio/captions/images/thumbnail/assembly; rebuilding audio invalidates captions + assembly; etc. Don't manually pass every downstream flag.

## Architecture

### Pipeline stages (in `main.py` → `run_pipeline()`)

The orchestrator runs six numbered steps, each calling into one module and caching its output:

1. **Script** — `modules/script.py`: OpenAI Responses API (`gpt-4.1` by default) generates a structured JSON script with `full_narration` and per-scene `narration_segment` + `image_prompt`. For series topics, `generate_series_script_package()` produces all connected parts at once. Cached to `output/topic_{id}_script.json`.
2. **Voiceover** — `modules/voiceover.py`: ElevenLabs → MP3 in `temp/topic_{id}_voiceover.mp3`.
3. **Images** — `modules/images.py`: OpenAI `gpt-image-2` (configurable; Replicate Flux and Stability are also wired up). One JPG per scene at `temp/topic_{id}_scene_NN.jpg`.
4. **Captions** — `modules/captions.py`: AssemblyAI returns word-level timestamps; cached as JSON at `temp/topic_{id}_captions.json`.
5. **Thumbnail** — `modules/images.py::generate_thumbnail_image()`.
6. **Assembly** — `modules/assembly.py`: **local FFmpeg renderer** (uses `imageio-ffmpeg` if available, else system `ffmpeg`). Replaces the older Creatomate/Supabase path described in the README. Burns captions, mixes background music from `music/`, outputs `output/topic_{id}_*.mp4`.

After step 6, `modules/publisher.py` posts to YouTube/TikTok/Instagram and `modules/tracker.py` writes a row to `logs/performance.csv`. The topic is then marked `done` in `topics.csv`.

### Cache invalidation — the non-obvious part

The caching system in `main.py` is the most subtle code in the repo. It uses three independent checks, all of which can force regeneration:

1. **Narration hash** (`_hash_text` of `script["full_narration"]`) stored in `temp/topic_{id}_cache_meta.json`. Mismatch ⇒ delete audio + captions + meta.
2. **mtime cascade**: if `script.json` is newer than `voiceover.mp3`, or `voiceover.mp3` is newer than `captions.json`, the downstream file is regenerated. The 1-second tolerance (`> mtime + 1`) is intentional — don't tighten it.
3. **Ordered content check** (`_captions_match_narration_ordered`): compares the first ~8 meaningful (non-stopword) tokens of captions vs narration *in order*. Catches stale captions from a previous script of the same topic that the hash/mtime checks would miss.

`cache_meta["narration_hash"]` is written **only after** captions are validated to match the current script (end of STEP 4). Writing it earlier was a real bug that let stale captions survive — don't move it.

### Scene-to-audio alignment

`_align_image_paths_to_words()` aligns each scene image to the real voiceover timing by finding the first meaningful tokens of each scene's `narration_segment` inside the AssemblyAI word stream. Failed matches are *interpolated* between successful neighbors (not thrown away). `_merge_short_image_intervals()` then merges any sub-3-second visual beats into adjacent scenes so images don't flash. Caption timing always comes from the audio; only image dwell time is smoothed.

### Configuration

`config.py` is a flat module of constants, imported directly (`from config import X`). Most modules guard each import with `try/except ImportError` and provide defaults, so adding a new config key is backwards-compatible — keep that pattern.

Switchable providers (relevant when debugging cost or quality):
- `SCRIPT_PROVIDER` / `SCRIPT_MODEL` — script generation (default OpenAI `gpt-4.1`).
- `IMAGE_PROMPT_PROVIDER` / `IMAGE_PROMPT_MODEL` — image-prompt refinement.
- `IMAGE_GENERATION_PROVIDER` — `openai_gpt_image` | `replicate_flux_ultra` | `stability_ultra` | `stability_sd3`.

### Dependencies

`requirements.txt` only lists `anthropic` and `requests`, but the code also uses `imageio-ffmpeg` (assembly) and optionally `cloudinary` (Instagram CDN, per README). If something is missing at runtime, install it directly rather than assuming requirements.txt is complete.

## Topics queue

`topics.csv` is the input queue. Columns: `id,title,series,part,description,status`. `status` is `pending` or `done`; `main.py` picks the first `pending` row. For standalone (non-series) topics, leave `series` empty and set `part` to `0` — the series-script package path is skipped in that case.

## Security note for any commit work

`config.py` currently contains **real, live API keys** committed in plaintext (Anthropic, OpenAI, ElevenLabs, AssemblyAI, Creatomate, fal.ai, Stability, Replicate, Supabase). If asked to commit, push, or share this file, surface this risk first — these need to be rotated and moved to env vars or a gitignored file. `config - Copy.py` is a stale backup of the same.
