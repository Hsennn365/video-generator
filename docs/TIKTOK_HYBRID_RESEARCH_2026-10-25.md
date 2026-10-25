# TikTok-first video-generator redesign — research and low-cost motion pilot
**Research updated 2026-10-25.** No paid API calls or publishing performed.

## Key decisions
1. KEEP this repository. It already supports OpenAI/Flux/Stability images, ElevenLabs voice, word-level captions, FFmpeg local assembly, music, SFX, caching, YouTube/TikTok/Instagram publishing. The README wrongly calls the current renderer Creatomate; `modules/assembly.py` now uses local FFmpeg.
2. Do not pay for 60-90 seconds of AI video per upload. Generate a few pivotal 4-5s moving shots and use *purpose-built moving visuals* for the rest (animated maps, timelines, diagrams, stock/licensed clips, FX/cutaways). Passive static slides alone are not a strong TikTok growth or Creator Rewards strategy.
3. Do not assume TikTok Creator Rewards is available for a Lebanon-region account. Confirm in TikTok Studio -> Monetization -> Creator Rewards. Region, residence, individual age and account eligibility matter. Never buy an account or use location spoofing to circumvent terms.
4. If/when eligible, focus original, story-driven **61-75 second** posts for rewards. Otherwise optimize hooks/retention on 30-45s experiments and cross-post to Shorts/Reels while exploring other legitimate revenue sources.
5. **No auto-posting and no monthly subscriptions** before a tested production sample and monetization check.

## Repository audit (actual code)
- `main.py` generates structured narration and scene descriptions; includes audio/caption/image hash+mtime cache logic and --rebuild-* flags.
- `modules/images.py` already supports `openai_gpt_image`, `replicate_flux_ultra`, and `stability_ultra` / `stability_sd3`. So "ChatGPT did all generation" is incorrect.
- `modules/assembly.py` currently composites still images, gentle zoom/pan on selected frames, captions, SFX, music using local FFmpeg.
- `config.example.py`: story count 15, duration 75 seconds, premium dark history style; the current visual references are a "dark historical graphic novel", not modern Zack-style CGI or Pixar.
- `outputs/scripts/singles/topic_16_script.json` contains "The Princes Who Vanished in the Tower", historical narrative segmented into ~6.5s still-image beats. Scene images depict different historical actors, often with no recurring character anchor; consistency is not enforced.
- `modules/tracker.py`: 48h metrics currently strongest for YouTube Analytics; TikTok tracking fields exist but robust TikTok analytics retrieval isn't implemented here.
- **Security**: `CLAUDE.md` says real API keys were *previously committed*. This repository is public. Rotate all possibly exposed provider keys, remove active plaintext secrets from file/history where possible, audit provider usage, disable risky keys. No credentials were accessed/printed by this research work.
- False README claims: Creatomate current renderer and $1.15 production cost are stale/unsupported by measured accounting; do not rely on them.

## Formats to test based on research
Official TikTok 2026 Trend Report stresses "curiosity detours," search relevance, original discovery rather than generic auto-pilot.
https://ads.tiktok.com/business/en-US/next

Metricool analyzed >2.3 million 2026 TikTok posts; more content alone doesn't buy reach; repeatable specific niches and high-signal hooks are useful.
https://metricool.com/tiktok-trends/

Revid analyzed 176,367 TikToks with 1,000+ likes (SELECTION BIAS: not typical account); faceless tutorials and storytelling can perform, but one must not promise median results.
https://www.revid.ai/blog/short-form-video-trends
- **A: Animated historical mystery** — narrated cliffhangers, moving illustrated/3D maps, parchment overlays, a few 4s character/action insert shots. Builds on existing Ash & Empires script assets. Distinctive channel niche.
- **B: Surprising real-world mechanism** — 60s explanation, actual stock prop footage, diagrams and vector animation. Avoid AI hallucinations for experiments.
- **C: Stylized CGI legend** — illustrated "game cutscene" keyframes + short image-to-video action clips + bold graphic cutaways; less factual detail, more narrative drama. Do not claim direct Zack D. clone.

Pilot recommendation: **A: Tower of London "Two princes vanished" (already scripted)**. Test 20-25s first with one strong 5s AI motion shot, two actual moving-map/graphic beats, and 1-2 still-enhanced visuals. Only expand to 61-75s after retention-style review and available revenue channel check.

## Image-generation specialization
Image choices by scene: current GPT Image 2 supports good typography and scene composition; FLUX.2 is a useful cinematic/mid-detail character image candidate; Seedream for inexpensive visual iteration; Nano Banana 2/Pro for image edits and character continuity; Recraft for strong graphics, titles/diagrams. Test same 3 prompts with two models before deciding. Avoid defaulting to "Pixar" as a universal style—the production needs an original consistent model sheet/visual bible. Rights/release/brand likenesses must be checked.
- Fal comparative price sources: https://fal.ai/learn/tools/best-text-to-image-apis-2026
- fal model catalog: https://fal.ai/models
- Current provider pricing is dynamic, verify per request at billing.

## Image-to-video: 2026 pay-per-use comparison
- FAL MiniMax H3 Max Turbo: listed **$0.04/sec at 768p**, $0.08/sec at 1080p (after 2026-09-14 promotion; verify endpoint actual current price), works from a source image.
  https://fal.ai/models/minimax/h3-max-turbo/image-to-video
- fal Kling 2.5 Turbo Pro: **$0.07/sec**, 5s $0.35.
  https://fal.ai/explore/image-to-video-apis
- Kling 3 Pro: ~$0.112/sec without audio (endpoint settings can change).
- Seedance 2 Fast: ~$0.2419/sec at 720p.
- Higher-priced models **not** required for every shot; no reason to pay for audio generation if ElevenLabs narrates.
A 65s video with only three 5s AI-generated moving shots:
    15s * $0.04/sec = **$0.60/video** nominal at 768p for H3 Max Turbo, before retries, image generation, voiceover, editing.
    One regeneration per shot roughly doubles that motion portion to ~$1.20/video.
    Whole 65s full AI motion would be $2.60/video (no retries); 1:1 cloud video is not sustainable at $20 monthly if many outputs are needed.
**Do not confuse 15s moving content with a guaranteed Creator Rewards-eligible original video.** Remaining timeline requires genuine moving graphics, footage, original explanation and edits, not a stale slideshow.

Official sources:
https://fal.ai/pricing
https://fal.ai/learn/serverless/ai-inference-provider-for-generative-media
https://fal.ai/explore/image-to-video-apis

## TikTok eligibility and policy
- Creator Rewards requires supported region, a personal account, age 18+, 10k followers, 100k views in 30 days, original content >=1 minute; confirm latest on account because country lists change.
- TikTok original-content criteria exclude purely photo slideshows, looping video and only text overlays.
- TikTok expects AI realistic visuals/audio to be labelled as AI generated.
https://support.tiktok.com/en/business-and-creator/creator-rewards-program/how-is-the-creator-rewards-program-different-from-the-tiktok-creator-fund
https://support.tiktok.com/en/using-tiktok/creating-videos/ai-generated-content
- Lebanon **not confirmed eligible**; third-party list indicates not included as of Oct 2026: https://monetizednow.com/tiktok-creator-rewards-lebanon. User must check TikTok Studio.

## NEW OPT-IN code (nothing billed, nothing publishes)
`modules/motion_clips.py`: validates a LOCAL manifest and normalizes external stock/AI/generated MP4 clips via existing FFmpeg.
`modules/assembly.py`: when manifest exists, scene N uses real video; scenes not mapped continue with unchanged image workflow. Voiceover/captions remain as existing pipeline.
`tests/test_motion_clips.py`: standard-library offline tests.
Manifest goes into `temp/<prefix>motion_clips.json`:
```json
{"scenes":[
  {"scene_number":1,"path":"temp/topic_16_scene_01_motion.mp4"},
  {"scene_number":4,"path":"temp/topic_16_scene_04_motion.mp4"}
]}
```
Paths must point to real local files; generated videos are NOT created automatically. The motion media's embedded audio is muted so ElevenLabs narration remains.
Run:
```bash
python -m unittest discover -s tests
python main.py --topic-id 16 --rebuild-assembly --preview
```
Only do this when assets and an existing config are ready, and review existing CLI semantics before running: the pipeline may call other APIs if media caches are missing. `--preview` stops publication, but may still incur generation charges if inputs are missing.
**Proposed safeguards:** add a strict local-only assembly test and cost cap before letting normal production generate videos or publish automatically.

## Release and production gates
Gate 1: Confirm supported Creator Rewards program in user's actual TikTok Studio, or choose alternative monetization target.
Gate 2: Choose one prior script and 5 high-impact visual beats; make 3 image styles to select art direction.
Gate 3: One 5s video clip test (cheap pay-per-use, preview first). If bad, abandon provider; don't regenerate whole video.
Gate 4: Test local manifest-based mixed assembly on 20-25s sample, no publishing.
Gate 5: Check originality, accuracy, style, retention and actual cost; only then expand and post a small batch manually.
