"""A2A adapter used by the waiter to reach the external kitchen."""

from restaurant_agent.kitchen.port import A2AKitchen, kitchen_failure

__all__ = ["A2AKitchen", "kitchen_failure"]
