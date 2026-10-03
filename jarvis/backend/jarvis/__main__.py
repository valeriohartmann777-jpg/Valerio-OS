"""Run the JARVIS backend: ``python -m jarvis``."""

from __future__ import annotations

import uvicorn

from jarvis.api.app import create_app
from jarvis.observability.logging import configure_logging
from jarvis.settings import load_settings


def main() -> None:
    settings = load_settings()
    configure_logging(settings.logging.level, settings.log_dir)
    uvicorn.run(
        create_app(settings),
        host=settings.server.host,
        port=settings.server.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
