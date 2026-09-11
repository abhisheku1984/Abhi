"""Characters, Voices and Avatars (§10, §13, §15)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError, SafetyBlockedError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Asset, Avatar, Character, Voice
from app.db.session import get_db
from app.safety import moderation
from app.services import prompt_engine

log = get_logger("api.entities")

characters = APIRouter(prefix="/characters", tags=["characters"])
voices = APIRouter(prefix="/voices", tags=["voices"])
avatars = APIRouter(prefix="/avatars", tags=["avatars"])

DEFAULT_LOCKS = {"character": True, "face": True, "costume": True, "style": True,
                 "environment": False, "voice": False}


class CharacterIn(BaseModel):
    name: str = Field(default="New character", max_length=160)
    description: str = ""
    profile: dict[str, Any] = {}
    locks: dict[str, bool] = {}
    reference_asset_ids: list[str] = []
    voice_id: Optional[str] = None
    project_id: Optional[str] = None


class VoiceIn(BaseModel):
    name: str = Field(default="New voice", max_length=160)
    engine: str = ""
    language: str = "en"
    accent: str = ""
    gender: str = "neutral"
    style: str = "neutral"
    emotion: str = "neutral"
    speed: float = 1.0
    pitch: float = 1.0
    sample_asset_id: Optional[str] = None
    is_cloned: bool = False
    consent: dict[str, Any] = {}


class AvatarIn(BaseModel):
    name: str = Field(default="New avatar", max_length=160)
    avatar_type: str = "corporate-presenter"
    profile: dict[str, Any] = {}
    voice_id: Optional[str] = None
    reference_asset_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# Characters
# --------------------------------------------------------------------------- #

def _character_out(character: Character) -> dict[str, Any]:
    return {
        **character.to_dict(exclude={"profile", "locks", "reference_asset_ids"}),
        "profile": character.profile,
        "locks": {**DEFAULT_LOCKS, **(character.locks or {})},
        "reference_asset_ids": character.reference_asset_ids or [],
        "created_at": character.created_at.isoformat() if character.created_at else None,
        "updated_at": character.updated_at.isoformat() if character.updated_at else None,
    }


@characters.get("")
def list_characters(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user),
                    project_id: Optional[str] = None) -> Any:
    query = db.query(Character).filter(Character.owner_id == current.id)
    if project_id:
        query = query.filter(Character.project_id == project_id)
    items = query.order_by(Character.updated_at.desc()).all()
    return {"items": [_character_out(c) for c in items], "total": len(items)}


@characters.post("", status_code=201)
def create_character(payload: CharacterIn, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)) -> Any:
    decision = moderation.check_text(payload.description)
    if not decision.allowed:
        raise SafetyBlockedError("; ".join(decision.reasons))
    character = Character(
        id=new_id("chr_"),
        owner_id=current.id,
        project_id=payload.project_id,
        name=payload.name,
        description=payload.description,
        profile=payload.profile or {},
        locks={**DEFAULT_LOCKS, **(payload.locks or {})},
        reference_asset_ids=payload.reference_asset_ids or [],
        voice_id=payload.voice_id,
    )
    db.add(character)
    db.commit()
    log.info("character_created", character_id=character.id)
    return _character_out(character)


@characters.get("/{character_id}")
def get_character(character_id: str, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(get_current_user)) -> Any:
    character = db.get(Character, character_id)
    if not character:
        raise NotFoundError("Character not found.")
    if character.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this character.")
    return _character_out(character)


@characters.patch("/{character_id}")
def update_character(character_id: str, payload: dict, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)) -> Any:
    character = db.get(Character, character_id)
    if not character:
        raise NotFoundError("Character not found.")
    if character.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this character.")
    for key in ("name", "description", "voice_id", "project_id", "thumbnail_asset_id"):
        if key in payload:
            setattr(character, key, payload[key])
    if "profile" in payload and isinstance(payload["profile"], dict):
        character.profile = {**(character.profile or {}), **payload["profile"]}
    if "locks" in payload and isinstance(payload["locks"], dict):
        character.locks = {**(character.locks or {}), **payload["locks"]}
    if "reference_asset_ids" in payload and isinstance(payload["reference_asset_ids"], list):
        character.reference_asset_ids = payload["reference_asset_ids"]
    db.commit()
    return _character_out(character)


@characters.delete("/{character_id}")
def delete_character(character_id: str, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)) -> Any:
    character = db.get(Character, character_id)
    if not character:
        raise NotFoundError("Character not found.")
    if character.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this character.")
    db.delete(character)
    db.commit()
    return {"ok": True, "id": character_id}


@characters.post("/{character_id}/prompt")
def character_prompt(character_id: str, payload: dict, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)) -> Any:
    """Build a consistency-locked prompt fragment for this character (§10)."""
    character = db.get(Character, character_id)
    if not character:
        raise NotFoundError("Character not found.")
    if character.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this character.")
    profile = character.profile or {}
    locks = {**DEFAULT_LOCKS, **(character.locks or {})}
    parts = []
    if locks.get("character"):
        parts.append(character.name)
    if locks.get("face") and profile.get("face"):
        parts.append(profile["face"])
    if locks.get("hair") is not False and profile.get("hair"):
        parts.append(profile["hair"])
    if profile.get("age_appearance"):
        parts.append(profile["age_appearance"])
    if locks.get("costume") and profile.get("clothing"):
        parts.append(profile["clothing"])
    if profile.get("accessories"):
        parts.append(profile["accessories"])
    if locks.get("style") and profile.get("style"):
        parts.append(profile["style"])
    action = str(payload.get("action") or "")
    environment = str(payload.get("environment") or "")
    if action:
        parts.append(action)
    if environment and locks.get("environment"):
        parts.append(environment)
    structured = prompt_engine.structure_prompt(", ".join(parts), style=str(payload.get("style", "cinematic")))
    return {
        "character_id": character_id,
        "locked_prompt": ", ".join(parts),
        "enhanced": prompt_engine.compose_prompt(structured),
        "structured": structured,
        "locks": locks,
    }


# --------------------------------------------------------------------------- #
# Voices
# --------------------------------------------------------------------------- #

def _voice_out(voice: Voice) -> dict[str, Any]:
    data = voice.to_dict(exclude={"consent"})
    consent = voice.consent or {}
    data["consent_complete"] = all(consent.get(f) for f in
                                   ("owner_attestation", "rights_holder", "authorised_by", "purpose"))
    data["consent_fields"] = sorted(consent.keys())
    return data


@voices.get("")
def list_voices(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user),
                language: Optional[str] = None) -> Any:
    query = db.query(Voice).filter(Voice.owner_id == current.id)
    if language:
        query = query.filter(Voice.language == language)
    items = query.order_by(Voice.updated_at.desc()).all()
    return {"items": [_voice_out(v) for v in items], "total": len(items),
            "languages": sorted(prompt_engine.LANGUAGE_NAMES.keys())}


@voices.post("", status_code=201)
def create_voice(payload: VoiceIn, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    if payload.is_cloned:
        decision = moderation.check_voice_clone({"is_cloned": True, "consent": payload.consent})
        if not decision.allowed:
            raise SafetyBlockedError("; ".join(decision.reasons),
                                     suggested_action="Complete the rights attestation for cloned voices.")
    voice = Voice(
        id=new_id("vox_"), owner_id=current.id, name=payload.name, engine=payload.engine,
        language=payload.language, accent=payload.accent, gender=payload.gender, style=payload.style,
        emotion=payload.emotion, speed=payload.speed, pitch=payload.pitch,
        sample_asset_id=payload.sample_asset_id, is_cloned=payload.is_cloned, consent=payload.consent,
    )
    db.add(voice)
    db.commit()
    log.info("voice_created", voice_id=voice.id, cloned=payload.is_cloned)
    return _voice_out(voice)


@voices.patch("/{voice_id}")
def update_voice(voice_id: str, payload: dict, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    voice = db.get(Voice, voice_id)
    if not voice:
        raise NotFoundError("Voice not found.")
    if voice.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this voice.")
    for key in ("name", "engine", "language", "accent", "gender", "style", "emotion", "speed", "pitch",
                "sample_asset_id", "status"):
        if key in payload:
            setattr(voice, key, payload[key])
    if "consent" in payload and isinstance(payload["consent"], dict):
        voice.consent = {**(voice.consent or {}), **payload["consent"]}
    db.commit()
    return _voice_out(voice)


@voices.delete("/{voice_id}")
def delete_voice(voice_id: str, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    voice = db.get(Voice, voice_id)
    if not voice:
        raise NotFoundError("Voice not found.")
    if voice.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this voice.")
    db.delete(voice)
    db.commit()
    return {"ok": True, "id": voice_id}


# --------------------------------------------------------------------------- #
# Avatars
# --------------------------------------------------------------------------- #

def _avatar_out(avatar: Avatar) -> dict[str, Any]:
    return {
        **avatar.to_dict(exclude={"profile"}),
        "profile": avatar.profile,
        "created_at": avatar.created_at.isoformat() if avatar.created_at else None,
        "updated_at": avatar.updated_at.isoformat() if avatar.updated_at else None,
    }


@avatars.get("")
def list_avatars(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    items = db.query(Avatar).filter(Avatar.owner_id == current.id).order_by(Avatar.updated_at.desc()).all()
    return {"items": [_avatar_out(a) for a in items], "total": len(items)}


@avatars.post("", status_code=201)
def create_avatar(payload: AvatarIn, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(get_current_user)) -> Any:
    avatar = Avatar(
        id=new_id("avt_"), owner_id=current.id, name=payload.name, avatar_type=payload.avatar_type,
        profile=payload.profile or {}, voice_id=payload.voice_id,
        reference_asset_id=payload.reference_asset_id,
    )
    db.add(avatar)
    db.commit()
    return _avatar_out(avatar)


@avatars.patch("/{avatar_id}")
def update_avatar(avatar_id: str, payload: dict, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(get_current_user)) -> Any:
    avatar = db.get(Avatar, avatar_id)
    if not avatar:
        raise NotFoundError("Avatar not found.")
    if avatar.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this avatar.")
    for key in ("name", "avatar_type", "voice_id", "reference_asset_id", "thumbnail_asset_id", "status"):
        if key in payload:
            setattr(avatar, key, payload[key])
    if "profile" in payload and isinstance(payload["profile"], dict):
        avatar.profile = {**(avatar.profile or {}), **payload["profile"]}
    db.commit()
    return _avatar_out(avatar)


@avatars.delete("/{avatar_id}")
def delete_avatar(avatar_id: str, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(get_current_user)) -> Any:
    avatar = db.get(Avatar, avatar_id)
    if not avatar:
        raise NotFoundError("Avatar not found.")
    if avatar.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this avatar.")
    db.delete(avatar)
    db.commit()
    return {"ok": True, "id": avatar_id}
