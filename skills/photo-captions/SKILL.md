---
name: photo-captions
description: Generate platform-tuned social media captions for photography. Use when a user shares a photo and wants captions for posting. Triggers on sharing photos with context like location, camera, lens, film stock, subject, or asking for captions/posts. Supports Instagram, Flickr, X/Twitter, Glass, Tumblr, Threads, Bluesky, 500px, Reddit, Facebook, VSCO, and Pinterest.
---

# Photo Captions

When the user shares a photo with context (location, camera, lens, film stock, subject, mood), generate captions for all platforms below in one response. Each platform has a distinct voice and format.

If the user specifies gear (camera body, lens, film stock, digital settings), include it where appropriate. Don't fabricate gear details the user didn't provide.

## Platforms

### 📸 Instagram
- **Tone**: Short, evocative, slightly poetic or witty. Let the image speak.
- **Format**: 1-2 line caption → blank line → gear line (if provided) → blank line → hashtags.
- **Hashtags**: Exactly 5 tags (Instagram's current limit). Pick the 5 most impactful: prioritize genre (e.g. `#filmphotography`), location, film stock/gear, and one mood/style tag. Quality over quantity.

### 📷 Flickr
- **Tone**: Slightly more descriptive and contemplative. Flickr audiences appreciate story and craft.
- **Format**: Italicized title, dash, then 1-3 sentences of context/story. End with gear info.
- **Include**: Location context, what drew the photographer to the shot. Think photo essay voice.

### 🐦 X (Twitter)
- **Tone**: Punchy, concise, dry. Under 280 characters ideally. No hashtag spam.
- **Format**: One strong line about the image. Gear at the end if it fits naturally.
- **Goal**: Makes someone stop scrolling.

### 🪟 Glass
- **Tone**: Photographer-to-photographer. Understated, genuine. No hashtags, no engagement bait.
- **Format**: 1-3 sentences. Location and brief observation. Gear on a separate line with middle dots (·) as separators.
- **Vibe**: Like talking to a friend at a photo walk.

### 🟦 Tumblr
- **Tone**: More literary, expressive, slightly longer. Tumblr appreciates mood and storytelling.
- **Format**: Bold location as title. 2-4 sentences of narrative/reflection. Gear line. Then tags.
- **Tags**: Use spaces in Tumblr tags: `#film photography` not `#filmphotography`. 8-12 tags.

### 🔵 Bluesky
- **Tone**: Conversational, warm, community-minded. Similar energy to early Twitter.
- **Format**: 1-2 sentences, casual but thoughtful. Under 300 characters. Gear mention optional.
- **No hashtags** unless they add real value (Bluesky culture leans anti-hashtag-spam).

### 🧵 Threads
- **Tone**: Casual, Instagram-adjacent but more conversational. Think talking to followers, not curating a gallery.
- **Format**: 1-2 sentences, relaxed. Gear mention if interesting. Minimal hashtags (3-5 max).

### 🔢 500px
- **Tone**: Technical and craft-focused. 500px is a photography-first community that values technique.
- **Format**: Title line, then 1-3 sentences covering the shot — technique, conditions, what made it work. Always include full gear details.
- **Include**: Camera settings, lighting conditions, or technique notes when available.

### 🟠 Reddit
- **Tone**: Authentic, slightly self-deprecating, community-friendly. No self-promotion vibes.
- **Format**: Post title (concise, descriptive) + comment body with context and gear.
- **Title**: Location or subject + gear in brackets, e.g. `Bombay Beach [Canon EOS 1V, Tri-X 400]`
- **Comment**: 2-3 sentences of context/story. Mention relevant subreddits: r/analog for film, r/photography for digital, r/streetphotography, r/LandscapePhotography, etc.

### 👤 Facebook
- **Tone**: Personal, conversational, like sharing with friends and family. Most accessible voice.
- **Format**: 2-3 casual sentences. Story-driven — where you were, what you were doing, why it caught your eye. Gear mention only if it adds to the story.
- **No hashtags** (or 1-2 at most). Facebook audiences care about the story, not the craft.

### 🎞️ VSCO
- **Tone**: Minimal, poetic, understated. VSCO is the quiet gallery — let the image breathe.
- **Format**: 1 line max. Sometimes just a single word or short phrase. No hashtags.
- **Vibe**: Think whispered, not announced. VSCO captions are closer to titles than descriptions. The less you say, the better.
- **No gear talk** unless it's film stock and even then, keep it subtle.

### 📌 Pinterest
- **Tone**: Descriptive and searchable. Pinterest is a discovery engine — think SEO meets aesthetics.
- **Format**: Clear, keyword-rich title (what it is + where) followed by 2-3 sentences that describe the scene, mood, and style. Include relevant keywords naturally.
- **Include**: Location, style (film photography, black and white, landscape), mood, and any relevant themes (road trip, desert, americana, etc.).
- **Goal**: Someone searching "desert film photography" or "Route 66 aesthetic" should find this pin.
- **No hashtags** — Pinterest uses keywords in descriptions for discovery, not tags.

## Guidelines

- Adapt all captions to the specific photo content, location, and mood.
- Don't repeat the same phrase across platforms. Each should feel native to its community.
- Humor and wit are welcome but should match the photo's mood.
- If the photo is black and white, add relevant B&W tags where appropriate.
- Never be generic. Every caption should feel written specifically for that image.
- For film photos, lean into the analog aesthetic. For digital, focus on the moment and technique.
- If the user only wants specific platforms, generate only those.
- Write like a human, not a copywriter. No emdashes (—), no semicolons for drama, no overly polished prose. Use periods, commas, and natural sentence breaks. If you wouldn't say it out loud, don't write it.
