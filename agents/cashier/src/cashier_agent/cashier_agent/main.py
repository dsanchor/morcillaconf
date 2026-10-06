"""Run the external cashier A2A service."""

import uvicorn
from dotenv import load_dotenv

from cashier_agent.app import create_app
from cashier_agent.config import Settings


def main() -> None:
    load_dotenv()
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.cashier_host,
        port=settings.cashier_port,
    )


if __name__ == "__main__":
    main()
