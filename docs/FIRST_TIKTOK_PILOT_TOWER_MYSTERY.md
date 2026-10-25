# First budget-controlled TikTok pilot: "The Two Princes Who Vanished"

This reuses the existing factual/script base `outputs/scripts/singles/topic_16_script.json`. This is a **shot plan**, not AI media that has already been generated.

## Creative direction
Test *stylized 3D historical mystery* rather than photoreal reenactment or unanimated slideshow.
- Clean, professional CGI storybook style; adult-proportion characters, moderately simplified clothing, slightly uncanny faces, readable silhouettes, soft volumetric atmosphere, warm torchlight and blue night.
- Recurring identities: Edward (12), Richard (younger), their uncle Richard III. Character consistency established with **a single legally created reference sheet for each**.
- Establish **two palettes** only: warm Tower interiors, cooler exteriors/modern mystery motifs.
- Avoid mimicking Pixar character IP and impersonating other creators' likenesses/voices.
- All events presented as *historical theories and documented facts*, not speculative conclusions about who killed the princes.
- Narration from licensed TTS and properly licensed music/SFX. AI-generated realistic recreations should be labeled under TikTok policy.

## 65-second original-video candidate: 12 shots

| No. | Approx secs | Content | Media | Incremental AI video cost |
|---|---:|---|---|---|
|1|0–5|Two princes walk toward the Tower gate — immediate hook|4–5s image-to-video*|YES|
|2|5–10|Historical map zoom from Europe to London, Tower pin|Native 2D animated map + archival labels|NO|
|3|10–16|King dies; royal seal and crown graphic|2.5D parallax collage + independent prop movement|NO|
|4|16–21|Two children enter castle hall, torch flicker|4–5s image-to-video*|YES|
|5|21–26|Timeline 1483 king dies → princes housed in Tower|Native animated timeline|NO|
|6|26–32|Uncle Richard becomes king; portraits + animated crown/arrow|2.5D poster + graphic transition|NO|
|7|32–37|Animated castle cross-section and inaccessible chambers|Custom diagram + camera and marker animation|NO|
|8|37–43|Map of power struggle with banners moving|Native animated map / masks|NO|
|9|43–49|Absence of public sightings, locked corridor key hole|Stock moving prop animation + 2.5D lighting|NO|
|10|49–54|Door closes; flicker candle on empty chamber|4–5s image-to-video*|YES|
|11|54–60|Competing theories — two silhouettes, marked 'unproven'|Animated original vector cards and portrait layers|NO|
|12|60–65|Mystery payoff, Tower graphic as channel branded ending|Custom moving graphic, cliffhanger/question|NO|

*Cost estimate: 15 seconds AI motion using H3 Max Turbo at $0.04/s 768p = **$0.60**, assuming 3x5 sec and zero retries. One retry of each AI shot increases that portion to $1.20. Images/voice/editing are extra; prices change.

Caution: native map graphics and 2.5D layers must be meaningful custom moving visuals, **not a disguised photo slideshow**. If finished runtime is >=60s but original-quality requirement is not met, TikTok may still exclude Creator Rewards.

## Two-art-style cheap test BEFORE video spending
Generate **3 still frames per art direction** from the same three planned shots:
- Variant A: stylized adult-proportion CGI (approved Rocketbox reference as a visual *category*, not the actual character for medieval era), soft skin/hair detail, simplified detailed wardrobe.
- Variant B: richly shaded animated-book historical illustration with layered matte silhouettes, charcoal-grain details, vibrant highlight colors.
Ensure prompt / seed / composition consistent where model permits. **Do not** buy 6 images and declare one automatically viral. Human visual gate first.
- Model candidates: GPT Image 2 (already wired in the repo), FLUX.2 pro (current integration is **FLUX 1.1 Ultra via Replicate**, not FLUX.2), Seedream 5 Lite or Nano Banana 2 (not yet integrated). Prefer reusing existing provider first, evaluate improved model only if necessary.
- Style sheet: key protagonist portrait, Tower exterior, interior with two silhouettes. Record consistent lighting/palette and identities.

## Motion test
Take the accepted style's Tower gate opener and ask for a **single action only**:
"Camera creeps forward toward the 15th-century Tower gate; the two children take two natural walking steps toward the shadowed arch; believable garment movement; consistent faces; no invented third child; no dialogue, text or logos."
Animate FIRST FRAME to 5 seconds 768p via pay-per-use MiniMax H3 Max Turbo, if user allows ~$0.20 quote and budget cap. **DO NOT call paid APIs before user approval.** Try the provider's nonbilled demo/free generations if explicitly offered and terms allow.

## Integration into existing generator
The *optional* hybrid manifest is `temp/<asset_prefix>motion_clips.json`; e.g. `temp/topic_16_motion_clips.json`.
Only when files already exist:
```json
{
  "scenes": [
    {"scene_number": 1, "path": "temp/topic_16_scene_01_motion.mp4"},
    {"scene_number": 4, "path": "temp/topic_16_scene_04_motion.mp4"},
    {"scene_number": 10, "path": "temp/topic_16_scene_10_motion.mp4"}
  ]
}
```
At assembly: these local clips become actual video segments. Remaining scenes use existing JPGs. For a 65s final, original scenes and timestamps must be edited to align to narration before rendering; don't blindly use an old 75/90s cached script. Mixed-media code strips AI native audio to preserve ElevenLabs TTS.

## Measurable success criteria before scaling
1. Hook grabs attention in first 2 seconds; actual video, not slow slideshow.
2. Video is fact-checked, intelligible, and identity consistent.
3. Complete 20–25s proof exported with motion clip(s) and working captions.
4. All-in spend and retry count logged. Separate fixed provider plan from per-use.
5. If TikTok Creator Rewards is unavailable in user region, pivot business plan to alternative approved revenue paths; do not purchase region-bypassing accounts.

Current blocker: user must verify Creator Rewards eligibility in TikTok Studio and approve the first real **paid API generation** before any credits are charged. Offline storyboard review and code tests can continue at zero media-generation cost.
