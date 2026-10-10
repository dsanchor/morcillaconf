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
EVENTS = """let BusinessEvents = dependencies
| where name == "restaurant.business.event"
| extend event_id=tostring(customDimensions["business.event.id"])
| where isnotempty(event_id)
| summarize arg_max(timestamp, *) by event_id
| extend name=tostring(customDimensions["business.event.name"]);
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
            """customMetrics
| where name == "restaurant.seating.occupied_guests"
| summarize arg_max(timestamp, valueSum) by cloud_RoleInstance
| summarize current=sum(valueSum)
| project ['Ocupación']=max_of(0.0, current)""",
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
        """customMetrics
| where name == "restaurant.seating.occupied_guests"
| extend Tipo=tostring(customDimensions["place.kind"])
| summarize arg_max(timestamp, valueSum) by cloud_RoleInstance, Tipo
| summarize current=sum(valueSum) by Tipo
| project Tipo, Personas=max_of(0.0, current)
| where Personas > 0""",
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
        """customMetrics
| where name == "restaurant.visit.time_to_seat"
| summarize total=sum(valueSum), samples=sum(valueCount) by bin(timestamp, $interval)
| project timestamp, Media=iff(samples == 0, 0.0, total / samples)""",
        x=0,
        y=26,
        w=12,
        h=8,
        unit="s",
    )
    add(
        "Duración de las visitas",
        "timeseries",
        """customMetrics
| where name == "restaurant.visit.duration"
| summarize total=sum(valueSum), samples=sum(valueCount) by bin(timestamp, $interval)
| project timestamp, Media=iff(samples == 0, 0.0, total / samples)""",
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
        """customMetrics
| where name == "restaurant.items.served"
| summarize Unidades=sum(valueSum) by Producto=tostring(customDimensions["carta_id"])
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
        """customMetrics
| where name == "restaurant.product.revenue"
| summarize Ingresos=sum(valueSum) by Producto=tostring(customDimensions["carta_id"])
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
        """customMetrics
| where name in ("restaurant.items.accepted", "restaurant.items.rejected",
                 "restaurant.items.served", "restaurant.items.paid")
| extend Producto=tostring(customDimensions["carta_id"]),
         Cantidad=valueSum
| where isnotempty(Producto)
| summarize Aceptadas=sumif(Cantidad, name == "restaurant.items.accepted"),
            Rechazadas=sumif(Cantidad, name == "restaurant.items.rejected"),
            Servidas=sumif(Cantidad, name == "restaurant.items.served"),
            Pagadas=sumif(Cantidad, name == "restaurant.items.paid") by Producto
| extend ['Conversión servido/pagado %']=round(100.0 * Pagadas / iff(Servidas == 0, 1.0, Servidas), 1)
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
        """customMetrics
| where name == "restaurant.items.paid"
| summarize Unidades=sum(valueSum) by Tipo=tostring(customDimensions["item.type"])""",
        x=16,
        y=43,
        w=8,
        h=9,
        result_format="table",
    )
    add(
        "Productos rechazados",
        "barchart",
        """customMetrics
| where name == "restaurant.items.rejected"
| extend Producto=tostring(customDimensions["carta_id"])
| where isnotempty(Producto)
| summarize Rechazadas=sum(valueSum) by Producto
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
items
| join kind=inner (items | project visit, product2=product) on visit
| where strcmp(product, product2) < 0
| summarize Visitas=dcount(visit) by Combination=strcat(product, " + ", product2)
| project ['Combinación']=Combination, Visitas
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
        """customMetrics
| where name == "restaurant.kitchen.orders"
| summarize ['Órdenes']=sum(valueSum) by Resultado=tostring(customDimensions["verdict"])""",
        x=0,
        y=61,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Resultado de barra",
        "piechart",
        """customMetrics
| where name == "restaurant.bar.rounds"
| summarize Rondas=sum(valueSum) by Resultado=tostring(customDimensions["verdict"])""",
        x=6,
        y=61,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Carga por estación",
        "barchart",
        """customMetrics
| where name == "restaurant.kitchen.lines.accepted"
| summarize Unidades=sum(valueSum) by ['Estación']=tostring(customDimensions["station"])""",
        x=12,
        y=61,
        w=6,
        h=8,
        result_format="table",
    )
    add(
        "Solicitudes ambiguas de barra",
        "stat",
        """customMetrics
| where name == "restaurant.bar.drinks.rejected"
  and tostring(customDimensions["reason.code"]) == "ambiguous"
| summarize Ambiguas=sum(valueSum)""",
        x=18,
        y=61,
        w=6,
        h=8,
    )
    add(
        "Duración de cocina y barra",
        "timeseries",
        """customMetrics
| where name in ("restaurant.kitchen.order_duration", "restaurant.bar.round_duration")
| extend Componente=iff(name == "restaurant.kitchen.order_duration", "cocina", "barra")
| summarize total=sum(valueSum), samples=sum(valueCount)
  by bin(timestamp, $interval), Componente
| project timestamp, Componente, Media=iff(samples == 0, 0.0, total / samples)""",
        x=0,
        y=69,
        w=12,
        h=8,
        unit="s",
    )
    add(
        "Fallos por componente y código",
        "table",
        """customMetrics
| where name == "restaurant.business.failures"
| summarize Fallos=sum(valueSum), ['Último']=max(timestamp)
  by Componente=tostring(customDimensions["failure.component"]),
     ['Código']=tostring(customDimensions["failure.code"])
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
| summarize Pagos=sum(valueSum) by ['Método']=tostring(customDimensions["payment.method"])""",
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
        """customMetrics
| where name in ("restaurant.revenue", "restaurant.payments.completed",
                 "restaurant.guests.paid")
| summarize revenue=sumif(valueSum, name == "restaurant.revenue"),
            payments=sumif(valueSum, name == "restaurant.payments.completed"),
            guests=sumif(valueSum, name == "restaurant.guests.paid")
  by bin(timestamp, $interval)
| project timestamp,
          ['Ticket medio']=iff(payments == 0, 0.0, revenue / payments),
          ['Coste por comensal']=iff(guests == 0, 0.0, revenue / guests)""",
        x=0,
        y=86,
        w=12,
        h=8,
        unit="currencyEUR",
    )
    add(
        "Pagos fallidos",
        "table",
        """customMetrics
| where name == "restaurant.payments.failed"
| summarize Fallos=sum(valueSum), ['Último']=max(timestamp)
  by ['Método']=tostring(customDimensions["payment.method"]),
     ['Código']=tostring(customDimensions["failure.code"])
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
| where name in ("restaurant.customers.first_visit", "restaurant.customers.returning")
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
| summarize Total=sum(valueSum) by ['Acción']=replace_string(name, "restaurant.memory.", "")""",
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
        """customMetrics
| where name in ("restaurant.visit.no_order", "restaurant.visit.no_payment")
| summarize Total=sum(valueSum)
  by Resultado=iff(name == "restaurant.visit.no_order", "Sin pedido", "Sin pago")""",
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
