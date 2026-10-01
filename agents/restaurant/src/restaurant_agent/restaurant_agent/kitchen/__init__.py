"""The kitchen: the chef that plans each order the waiter sends it.

Cocina v1 has only the chef (kitchen-lead). The station specialists, the
pantry and the order confirmation come later; the waiter reaches the chef
through ``KitchenPort``, so the chef can move out of this process unchanged
for the waiter.
"""

from restaurant_agent.kitchen.port import InProcessKitchen, kitchen_failure

__all__ = ["InProcessKitchen", "kitchen_failure"]
