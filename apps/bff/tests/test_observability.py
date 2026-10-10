from __future__ import annotations

import importlib

import morcillaconf_observability


def fresh_module():
    return importlib.reload(morcillaconf_observability)


def test_tracing_is_disabled_without_an_application_insights_connection(
    monkeypatch,
) -> None:
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    module = fresh_module()

    assert module.configure_tracing("test-service") is False


def test_tracing_configures_trace_and_metric_exporters(monkeypatch) -> None:
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000",
    )
    monkeypatch.setenv("APP_ENVIRONMENT", "test")
    module = fresh_module()
    configured: dict[str, object] = {}
    instrumented: list[bool] = []
    trace_exporter = object()
    metric_exporter = object()

    monkeypatch.setattr(
        module,
        "AzureMonitorTraceExporter",
        lambda **kwargs: configured.setdefault("trace_connection", kwargs)
        and trace_exporter,
    )
    monkeypatch.setattr(
        module,
        "AzureMonitorMetricExporter",
        lambda **kwargs: configured.setdefault("metric_connection", kwargs)
        and metric_exporter,
    )
    monkeypatch.setattr(
        module,
        "configure_otel_providers",
        lambda **kwargs: configured.update(kwargs),
    )

    class Instrumentor:
        def instrument(self) -> None:
            instrumented.append(True)

    monkeypatch.setattr(module, "HTTPXClientInstrumentor", Instrumentor)

    assert module.configure_tracing("test-service") is True
    assert configured["service_name"] == "test-service"
    assert configured["enable_sensitive_data"] is False
    assert configured["enable_message_events"] is False
    assert configured["exporters"] == [trace_exporter, metric_exporter]
    assert configured["resource_attributes"] == {
        "service.namespace": "morcillaconf",
        "deployment.environment.name": "test",
    }
    assert instrumented == [True]


def test_asgi_instrumentation_extracts_inbound_context(monkeypatch) -> None:
    module = fresh_module()
    middleware: list[tuple[object, dict[str, object]]] = []

    class App:
        def add_middleware(self, kind, **options) -> None:
            middleware.append((kind, options))

    monkeypatch.setattr(module, "configure_tracing", lambda service_name: True)
    app = App()

    assert module.instrument_asgi(app, "test-service") is app
    assert middleware == [
        (
            module.OpenTelemetryMiddleware,
            {"excluded_urls": "health,healthz,readiness"},
        )
    ]
