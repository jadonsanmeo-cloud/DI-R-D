"""ASGI compatibility entry point."""

from data_intelligence_api.app.factory import app, create_app

__all__ = ["app", "create_app"]
