"""API routers."""

from . import (  # noqa: F401
    assets,
    auth,
    characters,
    generate,
    jobs,
    projects,
    stories,
    system,
    workflows,
)

ROUTERS = [
    system.router,
    auth.router,
    projects.router,
    assets.router,
    jobs.router,
    generate.router,
    characters.router,
    stories.router,
    workflows.router,
]

__all__ = ["ROUTERS"]
