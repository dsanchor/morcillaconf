from dotenv import load_dotenv

from restaurant_agent.config import Settings
from restaurant_agent.remote import create_server


def main() -> None:
    load_dotenv()
    settings = Settings()
    create_server(settings).run()


if __name__ == "__main__":
    main()
