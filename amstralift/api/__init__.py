"""AMstraLift REST API package.

Exposes AMstraLift upgrade and security capabilities as an HTTP service
with automatic OpenAPI / Swagger documentation.
"""

from amstralift.api.app import create_app

__all__ = ["create_app"]
