"""API v1 aggregator."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin, assets, assistant, auth, editor, entities, files, generate, health, jobs,
    models, projects, prompts, settings, story, workflows, ws,
)

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(health.router)
api_router.include_router(settings.router)
api_router.include_router(projects.router)
api_router.include_router(assets.router)
api_router.include_router(files.router)
api_router.include_router(generate.router)
api_router.include_router(jobs.router)
api_router.include_router(models.router)
api_router.include_router(entities.characters)
api_router.include_router(entities.voices)
api_router.include_router(entities.avatars)
api_router.include_router(story.router)
api_router.include_router(editor.router)
api_router.include_router(workflows.router)
api_router.include_router(prompts.router)
api_router.include_router(assistant.router)
api_router.include_router(admin.router)
api_router.include_router(ws.router)
