"""Run the external kitchen A2A service."""

import uvicorn
from dotenv import load_dotenv

from kitchen_agent.app import create_app
from kitchen_agent.config import Settings


def main() -> None:
    load_dotenv()
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.kitchen_host,
        port=settings.kitchen_port,
    )


if __name__ == "__main__":
    main()
