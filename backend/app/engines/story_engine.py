"""Story engine: brief -> title, characters, script, scenes, shot list.

Deterministic planner (works offline, no LLM required). When an LLM adapter is
available it will be used to polish narration, but the structure - scene
count, shot count, durations, camera plan, transitions, voice and music cues -
is always produced by this engine so the pipeline is reproducible and testable.

Regeneration is per-scene: a single scene can be re-planned or re-rendered
without touching the rest of the project (spec §21).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.procedural_render import seed_from_text

ANIMALS = {
    "lion": ("Leo", "a proud lion with a golden mane"), "rabbit": ("Mimi", "a quick-witted rabbit"),
    "tiger": ("Raja", "a striped tiger"), "elephant": ("Ganesh", "a gentle elephant"),
    "fox": ("Rusty", "a clever fox"), "bear": ("Bruno", "a strong bear"),
    "wolf": ("Ash", "a lone wolf"), "monkey": ("Chintu", "a playful monkey"),
    "bird": ("Kiki", "a small songbird"), "owl": ("Sage", "a wise owl"),
    "deer": ("Chanda", "a shy deer"), "turtle": ("Slowpoke", "a patient turtle"),
    "cat": ("Milo", "a curious cat"), "dog": ("Bruno", "a loyal dog"),
    "horse": ("Storm", "a swift horse"), "fish": ("Bubbles", "a bright little fish"),
}
PEOPLE = {
    "teacher": ("Anita", "a warm, patient teacher"), "doctor": ("Dr. Rao", "a calm doctor"),
    "engineer": ("Arjun", "a focused engineer"), "farmer": ("Ramu", "a hardworking farmer"),
    "student": ("Meera", "a curious student"), "businessman": ("Kabir", "a sharp businessman"),
    "chef": ("Chef Mia", "a creative chef"), "artist": ("Nikhil", "a passionate artist"),
    "scientist": ("Dr. Sen", "a meticulous scientist"), "child": ("Riya", "a bright-eyed child"),
    "presenter": ("Sara", "a confident presenter"), "explorer": ("Vikram", "a brave explorer"),
}
GENRE_KEYWORDS = {
    "moral": ("lesson", "moral", "friendship", "kindness", "help", "together", "story", "tale"),
    "adventure": ("quest", "journey", "adventure", "explore", "discover", "treasure"),
    "educational": ("explain", "how", "learn", "teach", "what is", "guide", "tutorial"),
    "advertisement": ("advertisement", "ad for", "promote", "commercial", "launch", "brand", "campaign"),
    "corporate": ("company", "corporate", "business", "onboarding", "training", "report"),
    "social": ("reel", "shorts", "tiktok", "instagram", "social media", "viral"),
    "documentary": ("documentary", "history", "real story", "biography"),
}

STYLE_BUNDLES = {
    "anime": {"style": "anime", "palette": "vivid", "line": "clean line art"},
    "cartoon": {"style": "cartoon", "palette": "bright", "line": "rounded shapes"},
    "3d": {"style": "3d render", "palette": "soft", "line": "pixar-like"},
    "photoreal": {"style": "photoreal", "palette": "natural", "line": "cinematic"},
    "watercolor": {"style": "watercolor", "palette": "soft pastels", "line": "painterly"},
    "sketch": {"style": "sketch", "palette": "monochrome", "line": "pencil"},
}

CAMERA_PLAN = ["wide", "medium", "close-up", "medium", "wide"]
LENS_PLAN = ["24mm", "35mm", "50mm", "35mm", "18mm"]
TRANSITIONS = ["cut", "dissolve", "cut", "wipe", "fade"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class ShotPlan:
    number: str = "1"
    description: str = ""
    image_prompt: str = ""
    video_prompt: str = ""
    camera: str = "wide"
    lens: str = "35mm"
    motion: str = "normal"
    lighting: str = "cinematic"
    duration: float = 4.0
    transition: str = "cut"
    characters: list[str] = field(default_factory=list)
    dialogue: str = ""
    narration: str = ""
    sfx: list[str] = field(default_factory=list)
    music_cue: str = ""

    def to_dict(self) -> dict:
        return {
            "shot_number": self.number, "description": self.description,
            "image_prompt": self.image_prompt, "video_prompt": self.video_prompt,
            "camera": self.camera, "lens": self.lens, "motion": self.motion,
            "lighting": self.lighting, "duration_seconds": self.duration,
            "transition": self.transition, "characters": self.characters,
            "dialogue": self.dialogue, "narration": self.narration,
            "sound_effects": self.sfx, "music_cue": self.music_cue,
        }


@dataclass
class ScenePlan:
    index: int = 0
    title: str = ""
    description: str = ""
    narration: str = ""
    dialogue: list[dict] = field(default_factory=list)
    location: str = ""
    time_of_day: str = "day"
    mood: str = "calm"
    image_prompt: str = ""
    video_prompt: str = ""
    characters: list[str] = field(default_factory=list)
    music_cue: str = ""
    sfx: list[str] = field(default_factory=list)
    duration: float = 8.0
    shots: list[ShotPlan] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index, "title": self.title, "description": self.description,
            "narration": self.narration, "dialogue": self.dialogue, "location": self.location,
            "time_of_day": self.time_of_day, "mood": self.mood,
            "image_prompt": self.image_prompt, "video_prompt": self.video_prompt,
            "characters": self.characters, "music_cue": self.music_cue,
            "sound_effects": self.sfx, "duration_seconds": self.duration,
            "shots": [s.to_dict() for s in self.shots],
        }


@dataclass
class StoryPlan:
    title: str = ""
    logline: str = ""
    synopsis: str = ""
    genre: str = "moral"
    audience: str = "general"
    style: str = "cinematic"
    language: str = "en"
    total_duration: float = 60.0
    characters: list[dict] = field(default_factory=list)
    script: str = ""
    scenes: list[ScenePlan] = field(default_factory=list)
    music_plan: dict = field(default_factory=dict)
    voice_plan: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title, "logline": self.logline, "synopsis": self.synopsis,
            "genre": self.genre, "audience": self.audience, "style": self.style,
            "language": self.language, "total_duration": self.total_duration,
            "characters": self.characters, "script": self.script,
            "scenes": [s.to_dict() for s in self.scenes],
            "music_plan": self.music_plan, "voice_plan": self.voice_plan,
        }


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
def extract_characters(brief: str) -> list[dict]:
    low = (brief or "").lower()
    found: list[dict] = []
    for key, (name, desc) in ANIMALS.items():
        if re.search(rf"\b{key}s?\b", low):
            found.append({"name": name, "species": key, "description": desc,
                          "role": "protagonist" if len(found) == 0 else "supporting",
                          "voice_style": "warm and friendly"})
    for key, (name, desc) in PEOPLE.items():
        if re.search(rf"\b{key}s?\b", low):
            found.append({"name": name, "species": "human", "description": desc,
                          "role": "protagonist" if len(found) == 0 else "supporting",
                          "voice_style": "clear and confident"})
    # Capitalised names in the brief ("about Aryan and Zoya").
    # Imperative verbs and format words are never character names.
    NOT_NAMES = {
        "create", "make", "generate", "produce", "write", "tell", "show", "build",
        "design", "craft", "animated", "animation", "story", "stories", "video",
        "short", "shorts", "film", "minute", "minutes", "second", "seconds", "about",
        "cinematic", "cartoon", "reel", "reels", "tiktok", "youtube", "instagram",
        "advertisement", "commercial", "documentary", "corporate", "kids", "promo",
    }
    for token in re.findall(r"\b([A-Z][a-z]{2,})\b", brief or ""):
        if token.lower() in ANIMALS or token.lower() in PEOPLE:
            continue
        if token.lower() in NOT_NAMES:
            continue
        if any(token == c["name"] for c in found):
            continue
        found.append({"name": token, "species": "human", "description": f"{token}, a central character",
                      "role": "protagonist" if not found else "supporting", "voice_style": "natural and warm"})
    if not found:
        found = [
            {"name": "Asha", "species": "human", "description": "Asha, the curious hero of the story",
             "role": "protagonist", "voice_style": "warm and friendly"},
            {"name": "Kabir", "species": "human", "description": "Kabir, a loyal companion",
             "role": "supporting", "voice_style": "calm and steady"},
        ]
    return found[:5]


def detect_genre(brief: str) -> str:
    low = (brief or "").lower()
    best, best_pos = "moral", 10 ** 9
    for genre, words in GENRE_KEYWORDS.items():
        for w in words:
            pos = low.find(w)
            if 0 <= pos < best_pos:
                best, best_pos = genre, pos
    return best


def detect_duration(brief: str, default: float = 60.0) -> float:
    low = (brief or "").lower()
    m = re.search(r"(\d+)\s*(?:second|sec|s)\b", low)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:minute|min)\b", low)
    if m:
        return float(m.group(1)) * 60.0
    m = re.search(r"(\d+)\s*-\s*minute", low)
    if m:
        return float(m.group(1)) * 60.0
    return default


def extract_topic(brief: str) -> str:
    text = re.sub(r"^(create|make|generate|produce|write)\s+(a|an|the)?\s*", "", (brief or "").strip(), flags=re.I)
    text = re.sub(r"\b(\d+)\s*(second|sec|minute|min)s?\b", "", text, flags=re.I)
    text = re.sub(r"\b(animated|cinematic|short|video|story|film|about)\b", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" .,") or "an original story"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def _arc(genre: str) -> list[tuple[str, str]]:
    """(beat title, beat purpose) sequences per genre."""
    return {
        "moral": [
            ("Setting the scene", "Establish the world and introduce the main characters."),
            ("The meeting", "The characters meet and a small problem appears."),
            ("The challenge", "The problem grows and the characters must act."),
            ("The choice", "A decision reveals the characters' true nature."),
            ("The resolution", "The characters work together and the lesson lands."),
        ],
        "adventure": [
            ("The call", "Introduce the hero and the world they live in."),
            ("Departure", "The hero sets out on the journey."),
            ("The obstacle", "A serious obstacle blocks the way."),
            ("The discovery", "An unexpected discovery changes everything."),
            ("The return", "The hero returns, changed by the journey."),
        ],
        "educational": [
            ("Hook", "Open with a question the viewer wants answered."),
            ("The problem", "Show why the topic matters in real life."),
            ("Explanation", "Break the idea into a simple visual explanation."),
            ("Example", "Show a concrete worked example."),
            ("Recap", "Summarise the key takeaways."),
        ],
        "advertisement": [
            ("The hook", "Grab attention in the first two seconds."),
            ("The problem", "Show the pain point the audience recognises."),
            ("The product", "Introduce the product as the answer."),
            ("The proof", "Demonstrate the benefit in action."),
            ("The call to action", "Tell the viewer exactly what to do next."),
        ],
        "corporate": [
            ("Welcome", "Set the context and greet the audience."),
            ("The context", "Explain the current situation."),
            ("The approach", "Present the approach or solution."),
            ("The evidence", "Show results or data."),
            ("Next steps", "Close with clear next steps."),
        ],
        "social": [
            ("Hook", "Instantly arresting opening frame."),
            ("Build", "Raise tension or curiosity."),
            ("Payoff", "Deliver the payoff or reveal."),
        ],
        "documentary": [
            ("Opening image", "Establish place and time."),
            ("Context", "Introduce the subject and stakes."),
            ("Voices", "Present perspectives."),
            ("Turning point", "Reveal the moment that changed things."),
            ("Closing", "Reflect and close the loop."),
        ],
    }.get(genre, [("Opening", "Establish the scene."), ("Middle", "Develop the idea."), ("Close", "Conclude.")])


def _narration(genre: str, beat: str, brief_topic: str, names: list[str], index: int) -> str:
    a = names[0] if names else "our hero"
    b = names[1] if len(names) > 1 else "a friend"
    return {
        "moral": [
            f"Once upon a time, in a place full of quiet wonders, {a} began an ordinary day that would become unforgettable.",
            f"That was the day {a} met {b} - and discovered that things are not always what they seem.",
            f"Trouble arrived without warning, and {a} had to decide what kind of friend to be.",
            f"{a} chose kindness over fear, even though it was the harder path.",
            f"And so {brief_topic} taught everyone a simple lesson: courage grows when we share it.",
        ],
        "adventure": [
            f"Far beyond the maps, {a} had always wondered what lay past the horizon.",
            f"With nothing but a small bag and a great deal of nerve, {a} set out.",
            f"The way forward vanished. The storm rolled in. {a} had only one choice left.",
            f"What {a} found there changed the meaning of the journey entirely.",
            f"{a} came home with more than stories - and the horizon never looked the same.",
        ],
        "educational": [
            f"Have you ever wondered why {brief_topic} matters more than it seems?",
            f"Most of us meet this problem without even noticing it.",
            f"Here is the idea, one simple piece at a time.",
            f"Watch what happens when we put it into practice.",
            f"That is {brief_topic}, explained. Now you can use it too.",
        ],
        "advertisement": [
            f"Stop scrolling - this is the moment {brief_topic} gets easier.",
            f"You know the feeling: too much effort, not enough result.",
            f"Meet the simpler way to handle {brief_topic}.",
            f"Watch how quickly it works in real life.",
            f"Try it today, and feel the difference immediately.",
        ],
        "corporate": [
            f"Welcome. Today we look at {brief_topic} and why it matters to our teams.",
            f"Here is the situation as it stands right now.",
            f"This is the approach we recommend, and the reason it works.",
            f"The early results speak for themselves.",
            f"Here is what we do next, and who owns each step.",
        ],
        "social": [
            f"Wait for it - {brief_topic} like you have never seen it.",
            f"Things are about to get interesting.",
            f"And that is the moment everything changed.",
        ],
        "documentary": [
            f"This is the place where the story of {brief_topic} begins.",
            f"To understand it, we have to go back to the beginning.",
            f"Those who were there remember it differently.",
            f"Then came the decision that no one expected.",
            f"Decades later, the echoes are still here.",
        ],
    }.get(genre, [f"Scene {index + 1}: {beat}."])[min(index, 4) if genre != "social" else min(index, 2)]


def _dialogue_for(genre: str, index: int, names: list[str]) -> list[dict]:
    a = names[0] if names else "Hero"
    b = names[1] if len(names) > 1 else "Friend"
    bank = {
        "moral": [(a, "Do you really think we can do this?"), (b, "Together? Always.")],
        "adventure": [(a, "Whatever happens out there, we keep moving."), (b, "Then let's move.")],
        "educational": [(a, "So the simple version is...?"), (b, "Exactly - and that's the whole idea.")],
        "advertisement": [(a, "There has to be a better way."), (b, "There is now.")],
        "corporate": [(a, "What does this mean for the team?"), (b, "It means we move faster, with fewer surprises.")],
        "social": [(a, "You're not going to believe this."), (b, "Show me.")],
        "documentary": [(a, "I remember it clearly."), (b, "Everyone remembers it differently.")],
    }
    lines = bank.get(genre, [(a, "Let's begin.")])
    if index == 0:
        return [{"character": lines[0][0], "line": lines[0][1]}]
    if index == 1:
        return [{"character": lines[1][0], "line": lines[1][1]}]
    return []


MUSIC_BY_GENRE = {
    "moral": "children", "adventure": "cinematic", "educational": "documentary",
    "advertisement": "corporate", "corporate": "corporate", "social": "happy",
    "documentary": "documentary",
}
SFX_BY_GENRE = {
    "moral": [["birds", "wind"], ["footsteps"], ["heartbeat"], ["wind"], ["sparkle"]],
    "adventure": [["wind"], ["footsteps"], ["impact"], ["riser"], ["sparkle"]],
    "educational": [["pop"], ["whoosh"], ["pop"], ["notification"], ["pop"]],
    "advertisement": [["pop"], ["whoosh"], ["sparkle"], ["notification"], ["impact"]],
    "corporate": [["pop"], ["whoosh"], ["pop"], ["notification"], ["pop"]],
    "social": [["whoosh"], ["riser"], ["impact"]],
    "documentary": [["wind"], ["crowd"], ["wind"], ["drone"], ["wind"]],
}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def plan_story(brief: str, style: str = "cinematic", audience: str = "general",
               language: str = "en", target_duration: float | None = None,
               num_scenes: int | None = None, shots_per_scene: int = 2,
               use_llm: bool = True) -> StoryPlan:
    topic = extract_topic(brief)
    genre = detect_genre(brief)
    duration = target_duration or detect_duration(brief)
    characters = extract_characters(brief)
    names = [c["name"] for c in characters]
    beats = _arc(genre)

    if num_scenes and 1 <= num_scenes <= 12:
        if num_scenes < len(beats):
            beats = beats[:num_scenes]
        else:
            while len(beats) < num_scenes:
                beats.append((f"Beat {len(beats) + 1}", "Continue the story."))

    bundle = STYLE_BUNDLES.get((style or "cinematic").lower(), {"style": style, "palette": "rich", "line": "cinematic"})
    total_shots = len(beats) * max(1, shots_per_scene)
    shot_duration = max(1.5, duration / max(total_shots, 1))

    scenes: list[ScenePlan] = []
    for i, (beat_title, beat_purpose) in enumerate(beats):
        narration = _narration(genre, beat_title, topic, names, i)
        sfx_list = (SFX_BY_GENRE.get(genre) or [["whoosh"]] * 5)[min(i, len(SFX_BY_GENRE.get(genre, [["whoosh"]])) - 1)]
        scene_chars = names[:2] if i % 2 == 0 else names[:1]
        location = _location_for(genre, i, topic)
        image_prompt = (
            f"{beat_title.lower()} of {topic}: {beat_purpose.lower()} "
            f"featuring {', '.join(scene_chars) if scene_chars else 'the scene'}, "
            f"{location}, {bundle['style']} style, {bundle['palette']} palette, {bundle['line']}"
        )
        video_prompt = (
            f"{image_prompt}, cinematic camera movement, {_camera_word(i)} shot, "
            f"{'slow' if i % 3 == 0 else 'normal'} motion, natural lighting"
        )
        scene = ScenePlan(
            index=i, title=beat_title, description=beat_purpose, narration=narration,
            dialogue=_dialogue_for(genre, i, names), location=location,
            time_of_day=["morning", "midday", "afternoon", "golden hour", "dusk"][i % 5],
            mood=["calm", "curious", "tense", "hopeful", "triumphant"][i % 5],
            image_prompt=image_prompt, video_prompt=video_prompt,
            characters=scene_chars, music_cue=f"{MUSIC_BY_GENRE.get(genre,'cinematic')} bed, {['soft','building','intense','warm','resolving'][i % 5]}",
            sfx=list(sfx_list), duration=round(shot_duration * shots_per_scene, 2),
        )
        for s in range(shots_per_scene):
            cam_idx = (i * shots_per_scene + s) % len(CAMERA_PLAN)
            shot = ShotPlan(
                number=f"{i + 1}.{s + 1}",
                description=f"{beat_title} - {['establishing', 'action', 'reaction', 'detail', 'closing'][s % 5]} shot",
                image_prompt=f"{image_prompt}, {CAMERA_PLAN[cam_idx]} framing",
                video_prompt=f"{video_prompt}, {CAMERA_PLAN[cam_idx]} framing, {['slow push in','gentle pan','static','tracking','pull back'][s % 5]}",
                camera=CAMERA_PLAN[cam_idx], lens=LENS_PLAN[cam_idx],
                motion=["slow", "normal", "cinematic", "smooth", "normal"][s % 5],
                lighting=["daylight", "golden hour", "studio", "cinematic", "low key"][i % 5],
                duration=round(shot_duration, 2),
                transition=TRANSITIONS[(i * shots_per_scene + s) % len(TRANSITIONS)],
                characters=scene_chars,
                dialogue=(scene.dialogue[0]["line"] if scene.dialogue and s == 1 else ""),
                narration=narration if s == 0 else "",
                sfx=list(sfx_list),
                music_cue=scene.music_cue,
            )
            scene.shots.append(shot)
        scenes.append(scene)

    title = _title_for(topic, genre)
    plan = StoryPlan(
        title=title,
        logline=f"A {genre} story about {topic} - told in {len(scenes)} scenes.",
        synopsis=" ".join(s.narration for s in scenes),
        genre=genre, audience=audience, style=style, language=language,
        total_duration=round(sum(s.duration for s in scenes), 2),
        characters=characters,
        script=_script(scenes, title, names),
        scenes=scenes,
        music_plan={"genre": MUSIC_BY_GENRE.get(genre, "cinematic"), "duration": round(duration, 1),
                    "cues": [{"scene": s.index, "cue": s.music_cue} for s in scenes]},
        voice_plan={"language": language, "narrator": "warm and clear",
                    "character_voices": [{"character": c["name"], "style": c.get("voice_style", "natural")}
                                         for c in characters]},
    )

    if use_llm:
        plan = _polish_with_llm(plan, brief)
    return plan


def _location_for(genre: str, index: int, topic: str) -> str:
    palaces = {
        "moral": ["a sunlit clearing", "the old banyan tree", "the river bend", "the village path", "the hilltop at dusk"],
        "adventure": ["the edge of the known map", "a narrow mountain pass", "the storm wall", "the hidden valley", "the road home"],
        "educational": ["a clean studio", "a simple diagram board", "a real-world example", "a worked example", "a summary card"],
        "advertisement": ["a busy morning kitchen", "a cluttered desk", "a bright product stage", "a real customer's home", "a clean call-to-action frame"],
        "corporate": ["a modern meeting room", "a dashboard view", "a team workspace", "a results screen", "an action plan slide"],
        "social": ["a striking opening frame", "a fast-cut montage", "the payoff moment"],
        "documentary": ["the place itself", "the archive", "the interview room", "the turning point", "the present day"],
    }
    opts = palaces.get(genre, ["a cinematic location"])
    return opts[min(index, len(opts) - 1)]


def _camera_word(index: int) -> str:
    return ["wide establishing", "tracking", "close-up", "medium", "aerial"][index % 5]


def _title_for(topic: str, genre: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9']+", topic) if w.lower() not in
             {"a", "an", "the", "of", "and", "about", "for"}][:5]
    base = " ".join(w.capitalize() for w in words) or "Untitled"
    suffix = {"moral": "A Lesson Remembered", "adventure": "The Journey",
              "educational": "Explained", "advertisement": "Made Simple",
              "corporate": "In Focus", "social": "In 30 Seconds",
              "documentary": "The True Story"}.get(genre, "")
    return f"{base}: {suffix}" if suffix else base


def _script(scenes: list[ScenePlan], title: str, names: list[str]) -> str:
    lines = [f"# {title}", "", f"Characters: {', '.join(names)}", ""]
    for s in scenes:
        lines.append(f"## Scene {s.index + 1}: {s.title}")
        lines.append(f"[Location: {s.location} | Time: {s.time_of_day} | Mood: {s.mood}]")
        lines.append(f"NARRATION: {s.narration}")
        for d in s.dialogue:
            lines.append(f"{d['character'].upper()}: {d['line']}")
        for shot in s.shots:
            lines.append(f"  - SHOT {shot.number} [{shot.camera}, {shot.lens}, {shot.duration}s, {shot.transition}]: {shot.description}")
        lines.append("")
    return "\n".join(lines)


def _polish_with_llm(plan: StoryPlan, brief: str) -> StoryPlan:
    """Optional: improve narration only when an LLM is reachable."""
    try:
        from app.adapters.implementations.llm_adapters import best_llm

        llm = best_llm()
        if not llm:
            return plan
        system = "You are a professional screenwriter. Answer with strict JSON only."
        prompt = (
            "Rewrite the narration for each scene. Return JSON: "
            '{"narration": ["...", "..."]} with exactly '
            f"{len(plan.scenes)} items, keeping the same order, same meaning, same genre "
            f"({plan.genre}), audience {plan.audience}, language {plan.language}. "
            f"Brief: {brief}. Current narration: {[s.narration for s in plan.scenes]}"
        )
        import json

        raw = llm.complete(system, prompt, max_tokens=900, temperature=0.85)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1]) if start >= 0 and end > start else None
        items = (data or {}).get("narration") or []
        if len(items) == len(plan.scenes):
            for scene, text in zip(plan.scenes, items):
                if isinstance(text, str) and text.strip():
                    scene.narration = text.strip()
                    if scene.shots:
                        scene.shots[0].narration = text.strip()
            plan.synopsis = " ".join(s.narration for s in plan.scenes)
            plan.script = _script(plan.scenes, plan.title, [c["name"] for c in plan.characters])
    except Exception:
        pass
    return plan


def regenerate_scene(plan: StoryPlan, scene_index: int, instruction: str = "") -> ScenePlan:
    """Re-plan a single scene. Everything else keeps its identity (spec §21)."""
    if not (0 <= scene_index < len(plan.scenes)):
        raise IndexError(scene_index)
    scene = plan.scenes[scene_index]
    salt = seed_from_text(instruction or scene.title, scene_index) if instruction else (scene.index + 1) * 7919
    variations = [
        "Reimagined with a stronger visual hook",
        "Reimagined with a slower, more intimate pace",
        "Reimagined with heightened contrasts and drama",
        "Reimagined with a warmer, softer tone",
    ]
    variant = variations[salt % len(variations)]
    extra = f" {instruction}" if instruction else ""
    scene.title = f"{scene.title} (v{scene_index + 2})" if instruction else scene.title
    scene.description = f"{scene.description} {variant}.{extra}".strip()
    scene.narration = _narration(plan.genre, scene.title, extract_topic(plan.synopsis), [c["name"] for c in plan.characters], scene_index)
    scene.mood = ["calm", "curious", "tense", "hopeful", "triumphant"][(salt // 3) % 5]
    scene.time_of_day = ["morning", "midday", "afternoon", "golden hour", "dusk"][(salt // 5) % 5]
    scene.image_prompt = f"{scene.image_prompt.rstrip('.')}, {variant.lower()}.{extra}"
    scene.video_prompt = f"{scene.video_prompt.rstrip('.')}, {variant.lower()}.{extra}"
    for s in scene.shots:
        s.image_prompt = scene.image_prompt
        s.video_prompt = scene.video_prompt
        s.camera = CAMERA_PLAN[(salt + int(s.number.split(".")[-1] or 1)) % len(CAMERA_PLAN)]
        s.lens = LENS_PLAN[(salt + int(s.number.split(".")[-1] or 1)) % len(LENS_PLAN)]
    return scene


def plan_to_project_payload(plan: StoryPlan) -> dict:
    """Shape stored on Project.story and returned by /api/story/generate."""
    return plan.to_dict()
