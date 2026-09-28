"""OpenTelemetry spans for BFF commands and waiter turns.

Only the API is used: spans are no-ops until an SDK and exporter are
configured (Application Insights arrives in phase 9). Attributes never
contain message text, names or actor ids.
"""

from __future__ import annotations

from opentelemetry import trace

tracer = trace.get_tracer("morcillaconf.bff")
