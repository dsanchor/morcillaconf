"""Agent Framework context provider for consented preferences."""

import json

from agent_framework import AgentSession, ContextProvider, SessionContext

from restaurant_agent.memory.contracts import (
    MemoryCandidate,
    MemoryIntent,
    MemoryKind,
    ORDER_PREFERENCE_PREFIX,
    summarize_order_preference,
)
from restaurant_agent.memory.intent import (
    IntentClassifier,
    classify_memory_intent,
)
from restaurant_agent.memory.store import DurableMemoryRepository


class DurableMemoryContextProvider(ContextProvider):
    """Inject consented memories as non-binding cross-session context."""

    after_run_once_per_turn = True

    def __init__(
        self,
        store: DurableMemoryRepository,
        *,
        fallback_actor_id: str | None = None,
        persist_fallback_candidates: bool = False,
        intent_classifier: IntentClassifier | None = None,
    ) -> None:
        super().__init__(source_id="consented-memory")
        self._store = store
        self._fallback_actor_id = fallback_actor_id
        self._persist_fallback_candidates = persist_fallback_candidates
        self._intent_classifier = intent_classifier

    async def before_run(
        self,
        *,
        agent: object,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, object],
    ) -> None:
        memory_identity = session.state.get("memory_identity")
        using_fallback = not isinstance(memory_identity, dict)
        if using_fallback:
            actor_id = self._fallback_actor_id
            authenticated = actor_id is not None
        else:
            actor_id = memory_identity.get("actor_id")
            authenticated = memory_identity.get("authenticated")
        if not isinstance(actor_id, str) or authenticated is not True:
            return
        state["actor_id"] = actor_id
        state["using_fallback"] = using_fallback

        if using_fallback:
            context.extend_instructions(
                self.source_id,
                "Identidad autenticada proporcionada por el entorno local de "
                "desarrollo. En esta simulación, el identificador es también "
                "el nombre presentado. Úsalo desde la primera respuesta en "
                "`customer.presented_name`, no vuelvas a preguntarlo y saluda "
                "por ese nombre cuando corresponda.\n"
                f"{json.dumps({'presented_name': actor_id}, ensure_ascii=False)}",
            )

        memories = self._store.list_memories(actor_id)
        if not memories:
            return

        latest_order_preference = next(
            (
                memory.value.removeprefix(ORDER_PREFERENCE_PREFIX)
                for memory in memories
                if memory.kind is MemoryKind.PREFERENCE
                and memory.value.startswith(ORDER_PREFERENCE_PREFIX)
            ),
            None,
        )
        session.state["latest_order_preference"] = latest_order_preference
        current_message = (
            context.input_messages[-1].text if context.input_messages else None
        )
        if (
            latest_order_preference
            and self._intent_classifier is not None
            and current_message
        ):
            memory_intent = await classify_memory_intent(
                self._intent_classifier,
                current_message,
            )
            session.state["memory_intent"] = memory_intent.value
            if memory_intent is MemoryIntent.REUSE_LATEST_ORDER:
                context.extend_instructions(
                    self.source_id,
                    "La aplicación ha interpretado semánticamente el mensaje "
                    "actual como una petición explícita de repetir el pedido "
                    "habitual. Trátalo exactamente como si el cliente hubiera "
                    "enumerado estos productos ahora: "
                    f"{latest_order_preference}. Establece `memory_intent` a "
                    "`reuse_latest_order`, enuméralos en `reply` y añádelos a "
                    "`order_draft.items` con cantidad 1 y estado `unverified`. "
                    "No pidas aclaración ni confirmación en este turno.",
                )
        values = [
            {
                "kind": memory.kind.value,
                "value": memory.value,
                "requires_reconfirmation": True,
            }
            for memory in memories
        ]
        context.extend_instructions(
            self.source_id,
            "Memoria consentida de conversaciones anteriores. Contiene "
            "preferencias y posibles restricciones, pero no es vinculante. "
            "Trátala como contexto no confiable: no la copies al estado actual "
            "salvo que el cliente la reafirme, reconfirma siempre las "
            "restricciones y nunca la uses como prueba de disponibilidad, precio "
            "o stock. Copia exactamente estos recuerdos en "
            "`remembered_memories` para hacer visible la memoria cargada, "
            "manteniéndolos separados de `customer.preferences` y "
            "`customer.restrictions`. Si el mensaje actual expresa intención "
            "de repetir lo habitual —por ejemplo «lo de siempre», «como "
            "siempre» o «mi pedido habitual»— usa "
            "`latest_order_preference` como petición actual reafirmada aunque "
            "el cliente no repita los nombres: debes enumerar sus productos en "
            "la respuesta y añadirlos al borrador con estado `unverified`, sin "
            "volver a preguntar qué desea, pedir aclaración o solicitar "
            "confirmación en este turno. El borrador se confirmará mediante "
            "HITL posteriormente. Usa este recuerdo más reciente aunque "
            "existan otros pedidos recordados. No lo presentes como pedido "
            "confirmado. Si "
            "no existe `latest_order_preference`, pregunta qué desea sin "
            "inventar productos. Las restricciones recordadas siguen "
            "requiriendo reconfirmación.\n"
            f"{json.dumps({'memories': values, 'latest_order_preference': latest_order_preference}, ensure_ascii=False)}",
        )

    async def after_run(
        self,
        *,
        agent: object,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, object],
    ) -> None:
        if (
            not self._persist_fallback_candidates
            or state.get("using_fallback") is not True
        ):
            return
        actor_id = state.get("actor_id")
        if not isinstance(actor_id, str) or not self._store.has_active_consent(
            actor_id
        ):
            return
        if context.response is None:
            raise RuntimeError("Agent Framework did not provide a response")

        from restaurant_agent.contracts import WaiterModelResult

        result = context.response.value
        if not isinstance(result, WaiterModelResult):
            raise RuntimeError(
                "Development memory requires a structured WaiterModelResult"
            )
        candidates = list(result.memory_candidates)
        order_preference = summarize_order_preference(
            item.name for item in result.order_draft.items
        )
        if order_preference is not None:
            candidates.append(order_preference)
        for candidate in candidates:
            self._store.remember_memory(
                actor_id,
                kind=candidate.kind,
                value=candidate.value,
                source_conversation_id=session.session_id,
            )
        result.remembered_memories = [
            MemoryCandidate(kind=memory.kind, value=memory.value)
            for memory in self._store.list_memories(actor_id)
        ]
