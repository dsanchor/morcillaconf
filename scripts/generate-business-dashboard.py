#!/usr/bin/env python3
"""Generate the Azure Managed Grafana restaurant business dashboard."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DATASOURCE = {
    "type": "grafana-azure-monitor-datasource",
    "uid": "${DS_AZURE_MONITOR}",
}
EVENTS = """let BusinessEvents = customEvents
| where name startswith "restaurant."
| extend event_id=tostring(customDimensions["business.event.id"])
| where isnotempty(event_id)
| summarize arg_max(timestamp, *) by event_id;
"""


def query(text: str, result_format: str = "time_series") -> list[dict[str, Any]]:
    prefix = "" if text.lstrip().startswith("customMetrics") else EVENTS
    return [
        {
            "azureLogAnalytics": {
                "dashboardTime": True,
                "query": prefix + text,
                "resources": ["$applicationInsightsResourceId"],
                "resultFormat": result_format,
                "timeColumn": "timestamp",
            },
            "datasource": DATASOURCE,
            "queryType": "Azure Log Analytics",
            "refId": "A",
        }
    ]


def panel(
    panel_id: int,
    title: str,
    kind: str,
    text: str,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    unit: str = "short",
    description: str = "",
    result_format: str = "time_series",
) -> dict[str, Any]:
    options: dict[str, Any]
    if kind == "stat":
        options = {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
        }
    elif kind == "timeseries":
        options = {
            "legend": {"displayMode": "table", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        }
    elif kind == "piechart":
        options = {
            "displayLabels": ["name", "percent"],
            "legend": {"displayMode": "table", "placement": "right", "showLegend": True},
            "pieType": "pie",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
        }
    elif kind == "table":
        options = {"cellHeight": "sm", "showHeader": True}
    else:
        options = {
            "legend": {"displayMode": "table", "placement": "bottom", "showLegend": True},
            "orientation": "auto",
            "showValue": "always",
        }
    return {
        "datasource": DATASOURCE,
        "description": description,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "mappings": [],
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "id": panel_id,
        "options": options,
        "pluginVersion": "12.4.3",
        "targets": query(text, result_format),
        "title": title,
        "type": kind,
    }


def row(panel_id: int, title: str, y: int) -> dict[str, Any]:
    return {
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "id": panel_id,
        "panels": [],
        "title": title,
        "type": "row",
    }


def build() -> dict[str, Any]:
    panels: list[dict[str, Any]] = []
    panel_id = 1

    def add_row(title: str, y: int) -> None:
        nonlocal panel_id
        panels.append(row(panel_id, title, y))
        panel_id += 1

    def add(title: str, kind: str, text: str, **options: Any) -> None:
        nonlocal panel_id
        panels.append(panel(panel_id, title, kind, text, **options))
        panel_id += 1

    add_row("Resumen ejecutivo", 0)
    summary = [
        (
            "Asistentes",
            """customMetrics
| where name == "restaurant.guests.seated"
| summarize Asistentes=sum(valueSum)""",
            "short",
        ),
        (
            "Visitas",
            """customMetrics
| where name == "restaurant.visits.started"
| summarize Visitas=sum(valueSum)""",
            "short",
        ),
        (
            "Ingresos",
            """customMetrics
| where name == "restaurant.revenue"
| summarize Ingresos=sum(valueSum)""",
            "currencyEUR",
        ),
        (
            "Ticket medio",
            """customMetrics
| where name == "restaurant.bill.total"
| summarize total=sum(valueSum), bills=sum(valueCount)
| project ['Ticket medio']=iff(bills == 0, 0.0, total / bills)""",
            "currencyEUR",
        ),
        (
            "Coste por comensal",
            """customMetrics
| where name in ("restaurant.revenue", "restaurant.guests.paid")
| summarize amount=sumif(valueSum, name == "restaurant.revenue"),
            guests=sumif(valueSum, name == "restaurant.guests.paid")
| project ['Coste por comensal']=iff(guests == 0, 0.0, amount / guests)""",
            "currencyEUR",
        ),
        (
            "Conversión a pago",
            """customMetrics
| where name in ("restaurant.visits.started", "restaurant.payments.completed")
| summarize started=sumif(valueSum, name == "restaurant.visits.started"),
            paid=sumif(valueSum, name == "restaurant.payments.completed")
| project ['Conversión']=iff(started == 0, 0.0, 100.0 * paid / started)""",
            "percent",
        ),
        (
            "Ocupación actual",
            """BusinessEvents
| where name in ("restaurant.seating.confirmed", "restaurant.seating.released")
| summarize arg_max(timestamp, name, customDimensions) by visit=tostring(customDimensions["restaurant.visit.id"])
| where name == "restaurant.seating.confirmed"
| summarize Ocupación=sum(toint(customDimensions["restaurant.party.size"]))""",
            "short",
        ),
        (
            "Abandono",
            """customMetrics
| where name in ("restaurant.visits.started", "restaurant.visits.abandoned")
| summarize started=sumif(valueSum, name == "restaurant.visits.started"),
            abandoned=sumif(valueSum, name == "restaurant.visits.abandoned")
| project Abandono=iff(started == 0, 0.0, 100.0 * abandoned / started)""",
            "percent",
        ),
    ]
    for index, (title, kql, unit) in enumerate(summary):
        add(
            title,
            "stat",
            kql,
            x=(index % 4) * 6,
            y=1 + (index // 4) * 4,
            w=6,
            h=4,
            unit=unit,
        )

    add_row("Afluencia, ocupación y recorrido", 9)
    add(
        "Asistentes por intervalo",
        "timeseries",
        """customMetrics
| where name == "restaurant.guests.seated"
| summarize Asistentes=sum(valueSum) by bin(timestamp, $interval)
| order by timestamp asc""",
        x=0,
        y=10,
        w=12,
        h=8,
    )
    add(
        "Ingresos por intervalo",
        "timeseries",
        """customMetrics
| where name == "restaurant.revenue"
| summarize Ingresos=sum(valueSum) by bin(timestamp, $interval)
| order by timestamp asc""",
        x=12,
        y=10,
        w=12,
        h=8,
        unit="currencyEUR",
    )
    add(
        "Embudo de conversión",
        "barchart",
        """let visits=BusinessEvents | extend visit=tostring(customDimensions["restaurant.visit.id"]);
union
(visits | where name == "restaurant.visit.started" | summarize Valor=dcount(visit) | extend Etapa="1. Entrada"),
(visits | where name == "restaurant.seating.confirmed" | summarize Valor=dcount(visit) | extend Etapa="2. Sentados"),
(visits | where name == "restaurant.order.submitted" | summarize Valor=dcount(visit) | extend Etapa="3. Pedido"),
(visits | where name == "restaurant.item.served" | summarize Valor=dcount(visit) | extend Etapa="4. Servido"),
(visits | where name == "restaurant.bill.presented" | summarize Valor=dcount(visit) | extend Etapa="5. Cuenta"),
(visits | where name == "restaurant.payment.completed" | summarize Valor=dcount(visit) | extend Etapa="6. Pago")
| project Etapa, Valor | order by Etapa asc""",
        x=0,
        y=18,
        w=12,
        h=8,
        result_format="table",
    )
    add(
        "Ocupación por tipo",
        "piechart",
        """BusinessEvents
| where name in ("restaurant.seating.confirmed", "restaurant.seating.released")
| summarize arg_max(timestamp, name, customDimensions) by visit=tostring(customDimensions["restaurant.visit.id"])
| where name == "restaurant.seating.confirmed"
| summarize Personas=sum(toint(customDimensions["restaurant.party.size"]))
  by Tipo=tostring(customDimensions["place.kind"])""",
        x=12,
        y=18,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Tamaño de los grupos",
        "barchart",
        """BusinessEvents
| where name == "restaurant.seating.confirmed"
| summarize Grupos=count() by Comensales=toint(customDimensions["restaurant.party.size"])
| order by Comensales asc""",
        x=18,
        y=18,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Tiempo hasta sentarse",
        "timeseries",
        """let started=BusinessEvents | where name == "restaurant.visit.started"
| project visit=tostring(customDimensions["restaurant.visit.id"]), arrived=timestamp;
let seated=BusinessEvents | where name == "restaurant.seating.confirmed"
| project visit=tostring(customDimensions["restaurant.visit.id"]), seated=timestamp;
started | join kind=inner seated on visit
| extend seconds=datetime_diff("millisecond", seated, arrived) / 1000.0
| summarize Media=avg(seconds), P95=percentile(seconds, 95) by bin(seated, $interval)
| project timestamp=seated, Media, P95""",
        x=0,
        y=26,
        w=12,
        h=8,
        unit="s",
    )
    add(
        "Duración de las visitas",
        "timeseries",
        """let started=BusinessEvents | where name == "restaurant.visit.started"
| project visit=tostring(customDimensions["restaurant.visit.id"]), arrived=timestamp;
let closed=BusinessEvents | where name == "restaurant.visit.closed"
| project visit=tostring(customDimensions["restaurant.visit.id"]), closed=timestamp;
started | join kind=inner closed on visit
| extend seconds=datetime_diff("millisecond", closed, arrived) / 1000.0
| summarize Media=avg(seconds), P95=percentile(seconds, 95) by bin(closed, $interval)
| project timestamp=closed, Media, P95""",
        x=12,
        y=26,
        w=12,
        h=8,
        unit="s",
    )

    add_row("Demanda gastronómica", 34)
    add(
        "Productos servidos",
        "barchart",
        """BusinessEvents
| where name == "restaurant.item.served"
| summarize Unidades=sum(toint(customDimensions["restaurant.item.quantity"]))
  by Producto=tostring(customDimensions["carta_id"])
| top 15 by Unidades desc""",
        x=0,
        y=35,
        w=12,
        h=8,
        result_format="table",
    )
    add(
        "Ingresos por producto",
        "barchart",
        """BusinessEvents
| where name == "restaurant.item.paid"
| summarize Ingresos=sum(todouble(customDimensions["restaurant.item.line_total"]))
  by Producto=tostring(customDimensions["carta_id"])
| top 15 by Ingresos desc""",
        x=12,
        y=35,
        w=12,
        h=8,
        unit="currencyEUR",
        result_format="table",
    )
    add(
        "Embudo por producto",
        "table",
        """BusinessEvents
| where name in ("restaurant.item.accepted", "restaurant.item.rejected",
                 "restaurant.item.served", "restaurant.item.paid")
| extend Producto=tostring(customDimensions["carta_id"]),
         Cantidad=toint(customDimensions["restaurant.item.quantity"])
| where isnotempty(Producto)
| summarize Aceptadas=sumif(Cantidad, name == "restaurant.item.accepted"),
            Rechazadas=sumif(Cantidad, name == "restaurant.item.rejected"),
            Servidas=sumif(Cantidad, name == "restaurant.item.served"),
            Pagadas=sumif(Cantidad, name == "restaurant.item.paid") by Producto
| extend ['Conversión servido/pagado %']=round(100.0 * Pagadas / iff(Servidas == 0, 1, Servidas), 1)
| order by Pagadas desc""",
        x=0,
        y=43,
        w=16,
        h=9,
        result_format="table",
    )
    add(
        "Mix platos/bebidas",
        "piechart",
        """BusinessEvents
| where name == "restaurant.item.paid"
| summarize Unidades=sum(toint(customDimensions["restaurant.item.quantity"]))
  by Tipo=tostring(customDimensions["item.type"])""",
        x=16,
        y=43,
        w=8,
        h=9,
        result_format="table",
    )
    add(
        "Productos rechazados",
        "barchart",
        """BusinessEvents
| where name == "restaurant.item.rejected"
| extend Producto=tostring(customDimensions["carta_id"])
| where isnotempty(Producto)
| summarize Rechazadas=sum(toint(customDimensions["restaurant.item.quantity"])) by Producto
| top 15 by Rechazadas desc""",
        x=0,
        y=52,
        w=12,
        h=8,
        result_format="table",
    )
    add(
        "Combinaciones frecuentes",
        "table",
        """let items=BusinessEvents
| where name == "restaurant.item.paid"
| project visit=tostring(customDimensions["restaurant.visit.id"]),
          product=tostring(customDimensions["carta_id"])
| where isnotempty(product) | distinct visit, product;
items | join kind=inner items on visit
| where product < product1
| summarize Visitas=dcount(visit) by Combinación=strcat(product, " + ", product1)
| top 15 by Visitas desc""",
        x=12,
        y=52,
        w=12,
        h=8,
        result_format="table",
    )

    add_row("Cocina y barra", 60)
    add(
        "Resultado de cocina",
        "piechart",
        """BusinessEvents
| where name == "restaurant.kitchen.completed"
| summarize Órdenes=count() by Resultado=tostring(customDimensions["verdict"])""",
        x=0,
        y=61,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Resultado de barra",
        "piechart",
        """BusinessEvents
| where name == "restaurant.bar.completed"
| summarize Rondas=count() by Resultado=tostring(customDimensions["verdict"])""",
        x=6,
        y=61,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Carga por estación",
        "barchart",
        """BusinessEvents
| where name == "restaurant.item.accepted" and tostring(customDimensions["item.type"]) == "dish"
| summarize Unidades=sum(toint(customDimensions["restaurant.item.quantity"]))
  by Estación=tostring(customDimensions["station"])""",
        x=12,
        y=61,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Solicitudes ambiguas de barra",
        "stat",
        """BusinessEvents
| where name == "restaurant.item.rejected"
  and tostring(customDimensions["item.type"]) == "drink"
  and tostring(customDimensions["reason.code"]) == "ambiguous"
| summarize Ambiguas=sum(toint(customDimensions["restaurant.item.quantity"]))""",
        x=18,
        y=61,
        w=6,
        h=8,
    )
    add(
        "Duración de cocina y barra",
        "timeseries",
        """BusinessEvents
| where name in ("restaurant.kitchen.completed", "restaurant.bar.completed")
| extend Componente=iff(name == "restaurant.kitchen.completed", "cocina", "barra"),
         seconds=todouble(iff(name == "restaurant.kitchen.completed",
                    customDimensions["restaurant.kitchen.duration_seconds"],
                    customDimensions["restaurant.bar.duration_seconds"]))
| summarize Media=avg(seconds), P95=percentile(seconds, 95)
  by bin(timestamp, $interval), Componente""",
        x=0,
        y=69,
        w=12,
        h=8,
        unit="s",
    )
    add(
        "Fallos por componente y código",
        "table",
        """BusinessEvents
| where name endswith ".failed"
| summarize Fallos=count(), Último=max(timestamp)
  by Componente=tostring(customDimensions["failure.component"]),
     Código=tostring(customDimensions["failure.code"])
| order by Fallos desc""",
        x=12,
        y=69,
        w=12,
        h=8,
        result_format="table",
    )

    add_row("Facturación e ingresos", 77)
    add(
        "Métodos de pago",
        "piechart",
        """customMetrics
| where name == "restaurant.payments.completed"
| summarize Pagos=sum(valueSum) by Método=tostring(customDimensions["payment.method"])""",
        x=0,
        y=78,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Distribución de tickets",
        "barchart",
        """BusinessEvents
| where name == "restaurant.payment.completed"
| extend Importe=todouble(customDimensions["restaurant.payment.amount"])
| extend Tramo=case(Importe < 10, "< 10 €", Importe < 20, "10-20 €",
                    Importe < 40, "20-40 €", Importe < 60, "40-60 €", ">= 60 €")
| summarize Tickets=count() by Tramo""",
        x=6,
        y=78,
        w=9,
        h=8,
        result_format="table",
    )
    add(
        "Cuentas presentadas y recalculadas",
        "barchart",
        """customMetrics
| where name in ("restaurant.bills.presented", "restaurant.bills.superseded")
| summarize Total=sum(valueSum)
  by Estado=iff(name == "restaurant.bills.presented", "Presentadas", "Recalculadas")""",
        x=15,
        y=78,
        w=9,
        h=8,
        result_format="table",
    )
    add(
        "Ticket y coste por comensal",
        "timeseries",
        """BusinessEvents
| where name == "restaurant.payment.completed"
| extend amount=todouble(customDimensions["restaurant.payment.amount"]),
         guests=toint(customDimensions["restaurant.party.size"])
| summarize ['Ticket medio']=avg(amount),
            ['Coste por comensal']=sum(amount) / sum(guests)
  by bin(timestamp, $interval)""",
        x=0,
        y=86,
        w=12,
        h=8,
        unit="currencyEUR",
    )
    add(
        "Pagos fallidos",
        "table",
        """BusinessEvents
| where name == "restaurant.payment.failed"
| summarize Fallos=count(), Último=max(timestamp)
  by Método=tostring(customDimensions["payment.method"]),
     Código=tostring(customDimensions["failure.code"])
| order by Fallos desc""",
        x=12,
        y=86,
        w=12,
        h=8,
        result_format="table",
    )

    add_row("Fidelización y experiencia", 94)
    add(
        "Primera visita frente a recurrentes",
        "piechart",
        """customMetrics
| where name in ("restaurant.customers.first_visit", "restaurant.customers.returning_visit")
| summarize Visitas=sum(valueSum)
  by Tipo=iff(name == "restaurant.customers.first_visit", "Primera visita", "Recurrente")""",
        x=0,
        y=95,
        w=8,
        h=8,
        result_format="table",
    )
    add(
        "Uso de memoria",
        "barchart",
        """customMetrics
| where name in ("restaurant.memory.updated", "restaurant.memory.reuse_requested",
                 "restaurant.memory.reuse_applied", "restaurant.memory.reuse_abandoned")
| summarize Total=sum(valueSum) by Acción=replace_string(name, "restaurant.memory.", "")""",
        x=8,
        y=95,
        w=8,
        h=8,
        result_format="table",
    )
    add(
        "Turnos por visita",
        "barchart",
        """BusinessEvents
| where name == "restaurant.conversation.turn_completed"
| summarize arg_max(timestamp, customDimensions) by visit=tostring(customDimensions["restaurant.visit.id"])
| summarize Visitas=count() by Turnos=toint(customDimensions["restaurant.conversation.turn_count"])
| order by Turnos asc""",
        x=16,
        y=95,
        w=8,
        h=8,
        result_format="table",
    )
    add(
        "Tiempo entre etapas",
        "table",
        """let stages=BusinessEvents
| where name in ("restaurant.visit.started", "restaurant.seating.confirmed",
                 "restaurant.order.submitted", "restaurant.item.served",
                 "restaurant.bill.presented", "restaurant.payment.completed")
| extend visit=tostring(customDimensions["restaurant.visit.id"])
| summarize Entrada=minif(timestamp, name == "restaurant.visit.started"),
            Asiento=minif(timestamp, name == "restaurant.seating.confirmed"),
            Pedido=minif(timestamp, name == "restaurant.order.submitted"),
            Servido=minif(timestamp, name == "restaurant.item.served"),
            Cuenta=minif(timestamp, name == "restaurant.bill.presented"),
            Pago=minif(timestamp, name == "restaurant.payment.completed") by visit;
stages
| summarize ['Entrada → asiento (s)']=avg(datetime_diff("millisecond", Asiento, Entrada))/1000.0,
            ['Asiento → pedido (s)']=avg(datetime_diff("millisecond", Pedido, Asiento))/1000.0,
            ['Pedido → servido (s)']=avg(datetime_diff("millisecond", Servido, Pedido))/1000.0,
            ['Servido → cuenta (s)']=avg(datetime_diff("millisecond", Cuenta, Servido))/1000.0,
            ['Cuenta → pago (s)']=avg(datetime_diff("millisecond", Pago, Cuenta))/1000.0""",
        x=0,
        y=103,
        w=16,
        h=7,
        unit="s",
        result_format="table",
    )
    add(
        "Visitas sin pedido o sin pago",
        "barchart",
        """BusinessEvents
| where name == "restaurant.visit.closed"
  and tostring(customDimensions["restaurant.visit.paid"]) == "False"
| summarize Total=count()
  by Resultado=iff(tostring(customDimensions["restaurant.visit.had_served_items"]) == "True",
                   "Servido sin pago", "Sin pedido")""",
        x=16,
        y=103,
        w=8,
        h=7,
        result_format="table",
    )

    return {
        "__inputs": [
            {
                "name": "DS_AZURE_MONITOR",
                "label": "Azure Monitor",
                "description": "",
                "type": "datasource",
                "pluginId": "grafana-azure-monitor-datasource",
                "pluginName": "Azure Monitor",
            }
        ],
        "__elements": {},
        "__requires": [
            {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "11.6.3"},
            {
                "type": "datasource",
                "id": "grafana-azure-monitor-datasource",
                "name": "Azure Monitor",
                "version": "12.4.3",
            },
        ],
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                }
            ]
        },
        "description": "KPIs de negocio del restaurante multiagente MorcillaConf.",
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": panels,
        "preload": False,
        "refresh": "1m",
        "schemaVersion": 42,
        "tags": ["morcillaconf", "business", "restaurant", "agent-framework"],
        "templating": {
            "list": [
                {
                    "current": {},
                    "datasource": DATASOURCE,
                    "description": "Suscripción de Application Insights",
                    "label": "Subscription",
                    "name": "subscriptionId",
                    "options": [],
                    "query": {
                        "grafanaTemplateVariableFn": {
                            "kind": "SubscriptionsQuery",
                            "rawQuery": "subscriptions()",
                        },
                        "queryType": "Azure Subscriptions",
                        "refId": "A",
                    },
                    "refresh": 1,
                    "type": "query",
                },
                {
                    "current": {},
                    "datasource": DATASOURCE,
                    "description": "Recurso de Application Insights",
                    "label": "Application Insights",
                    "name": "applicationInsightsResourceId",
                    "options": [],
                    "query": {
                        "azureLogAnalytics": {"query": "", "resources": []},
                        "azureResourceGraph": {
                            "query": 'resources | where ["type"] =~ "microsoft.insights/components" | distinct id'
                        },
                        "queryType": "Azure Resource Graph",
                        "refId": "A",
                        "subscriptions": ["$subscriptionId"],
                    },
                    "refresh": 1,
                    "type": "query",
                },
                {
                    "auto": True,
                    "auto_count": 30,
                    "auto_min": "1m",
                    "current": {"text": "$__auto", "value": "$__auto"},
                    "label": "Interval",
                    "name": "interval",
                    "options": [],
                    "query": "5m,15m,30m,1h,6h,12h,1d",
                    "refresh": 2,
                    "type": "interval",
                },
            ]
        },
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": "MorcillaConf · Negocio del restaurante",
        "uid": "morcillaconf-business",
        "version": 1,
        "weekStart": "",
    }


def main() -> None:
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if destination is None:
        sys.stdout.write(payload)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
