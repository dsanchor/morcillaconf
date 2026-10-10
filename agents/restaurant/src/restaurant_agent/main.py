from dotenv import load_dotenv
from morcillaconf_observability import configure_tracing

from restaurant_agent.config import Settings
from restaurant_agent.remote import create_server


def main() -> None:
    load_dotenv()
    settings = Settings()
    configure_tracing("morcillaconf-waiter")
    create_server(settings).run()


if __name__ == "__main__":
    main()
