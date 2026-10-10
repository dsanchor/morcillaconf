"""Shared trace-only OpenTelemetry setup for the restaurant services."""

from __future__ import annotations

import os
from threading import Lock
from typing import Any

from agent_framework.observability import configure_otel_providers
from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

_CONFIGURATION_LOCK = Lock()
_CONFIGURED_SERVICE: str | None = None


def configure_tracing(service_name: str) -> bool:
    """Configure Agent Framework and HTTP client tracing for one process."""

    global _CONFIGURED_SERVICE

    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if _sdk_disabled() or not connection_string:
        return False

    with _CONFIGURATION_LOCK:
        if _CONFIGURED_SERVICE is not None:
            if _CONFIGURED_SERVICE != service_name:
                raise RuntimeError(
                    "OpenTelemetry is already configured for "
                    f"{_CONFIGURED_SERVICE}, not {service_name}"
                )
            return True

        configure_otel_providers(
            service_name=service_name,
            resource_attributes={
                "service.namespace": "morcillaconf",
                "deployment.environment.name": os.getenv(
                    "APP_ENVIRONMENT", "development"
                ),
            },
            enable_sensitive_data=False,
            enable_message_events=False,
            exporters=[
                AzureMonitorTraceExporter(connection_string=connection_string)
            ],
        )
        HTTPXClientInstrumentor().instrument()
        _CONFIGURED_SERVICE = service_name
        return True


def instrument_asgi(app: Any, service_name: str) -> Any:
    """Add inbound W3C trace extraction while preserving the ASGI app type."""

    if configure_tracing(service_name):
        app.add_middleware(
            OpenTelemetryMiddleware,
            excluded_urls="health,healthz,readiness",
        )
    return app


def _sdk_disabled() -> bool:
    return os.getenv("OTEL_SDK_DISABLED", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


__all__ = ["configure_tracing", "instrument_asgi"]
