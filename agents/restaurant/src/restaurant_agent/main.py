from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv

from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.config import Settings
from restaurant_agent.memory import create_memory_store


def main() -> None:
    load_dotenv()
    settings = Settings()
    memory_store = create_memory_store(settings)
    if (
        settings.enable_dev_fake_identity
        and settings.dev_fake_memory_consent
        and settings.dev_fake_actor_id
    ):
        memory_store.grant_consent(
            settings.dev_fake_actor_id,
            source="development-environment",
        )
    server = ResponsesHostServer(
        create_waiter_agent(settings, memory_store=memory_store)
    )
    server.run()


if __name__ == "__main__":
    main()
