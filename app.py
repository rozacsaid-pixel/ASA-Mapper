"""Entrypoint for hosts that import a module-level ASGI app (e.g. Vercel)."""
from asa_mapper.api import create_app

app = create_app()
