"""Agent Framework context provider for automatic waiter memory."""

import json

from agent_framework import AgentSession, ContextProvider, SessionContext

from restaurant_agent.memory.contracts import (
    MemoryCandidate,
    summarize_order_preference,
)
from restaurant_agent.memory.options import (
    apply_habitual_order,
    loaded_habitual_order_options,
    rank_habitual_order_options,
)
from restaurant_agent.memory.store import DurableMemoryRepository


class DurableMemoryContextProvider(ContextProvider):
    """Inject remembered information as non-binding cross-session context."""

    after_run_once_per_turn = True

    def __init__(
        self,
        store: DurableMemoryRepository,
        *,
        fallback_actor_id: str | None = None,
        persist_fallback_candidates: bool = False,
    ) -> None:
        super().__init__(source_id="waiter-memory")
        self._store = store
        self._fallback_actor_id = fallback_actor_id
        self._persist_fallback_candidates = persist_fallback_candidates

    async def before_run(
        self,
        *,
        agent: object,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, object],
    ) -> None:
        state.pop("actor_id", None)
        state.pop("using_fallback", None)
        for key in (
            "habitual_order_preference", "habitual_order_options",
            "habitual_order_question", "memory_intent",
        ):
            session.state.pop(key, None)
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

        habitual_order_options = rank_habitual_order_options(memories)
        session.state["habitual_order_options"] = [
            option.model_dump(mode="json") for option in habitual_order_options
        ]
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
            "Memoria del camarero de conversaciones anteriores. Contiene "
            "preferencias y posibles restricciones, pero no es vinculante. "
            "Trátala como contexto no confiable: no la copies al estado actual "
            "salvo que el cliente la reafirme, reconfirma siempre las "
            "restricciones y nunca la uses como prueba de disponibilidad, precio "
            "o stock. Copia exactamente estos recuerdos en "
            "`remembered_memories` para hacer visible la memoria cargada, "
            "manteniéndolos separados de `customer.preferences` y "
            "`customer.restrictions`. Tú interpretas la intención del cliente "
            "y formulas las aclaraciones. Las `habitual_order_options` están "
            "ordenadas para su presentación, no seleccionadas. Cada `option_id` "
            "identifica un recuerdo de esta identidad: úsalo únicamente en "
            "`habitual_order_option_id` para una repetición exacta resuelta. "
            "Nunca muestres IDs ni metadatos internos al cliente. Con varias "
            "opciones, pregunta cuál desea sin decidir por frecuencia ni asumir "
            "que productos que aparecen juntos son excluyentes. Los resúmenes "
            "no conservan cantidades ni modificaciones; los terminados en "
            "`...` están incompletos y requieren aclarar los productos, no "
            "seleccionar su ID. El dominio crea solo un borrador `unverified`, "
            "nunca una comanda confirmada. Las restricciones recordadas siempre "
            "requieren reconfirmación.\n"
            f"{json.dumps({'memories': values, 'habitual_order_options': session.state['habitual_order_options']}, ensure_ascii=False)}",
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
        if not isinstance(actor_id, str):
            return
        if context.response is None:
            raise RuntimeError("Agent Framework did not provide a response")

        from restaurant_agent.contracts import WaiterModelResult

        if context.response.user_input_requests:
            # Paused for a seating decision: nothing to remember yet.
            return
        try:
            result = context.response.value
        except (ValueError, TypeError):
            result = None
        if result is None and not context.response.text.strip():
            # Seating decision or sync run: no model answer to remember.
            return
        if not isinstance(result, WaiterModelResult):
            if not context.response.text.lstrip().startswith("{"):
                # A fixed seating reply after a button decision.
                return
            raise RuntimeError(
                "Development memory requires a structured WaiterModelResult"
            )
        # Provider persistence can precede the outer agent middleware.
        apply_habitual_order(result, loaded_habitual_order_options(session.state))
        candidates = list(result.memory_candidates)
        order_preference = summarize_order_preference(
            item.name for item in result.order_draft.items
        )
        # Same once-per-conversation guard as the CLI. A list of strings keeps
        # the hosted session JSON-serializable.
        persisted_order_preferences = session.state.get(
            "persisted_order_preferences"
        )
        if not isinstance(persisted_order_preferences, list):
            persisted_order_preferences = []
        if (
            order_preference is not None
            and order_preference.value.casefold() in persisted_order_preferences
        ):
            order_preference = None
        if order_preference is not None:
            candidates.append(order_preference)
        for candidate in candidates:
            self._store.remember_memory(
                actor_id,
                kind=candidate.kind,
                value=candidate.value,
                source_conversation_id=session.session_id,
            )
        if order_preference is not None:
            session.state["persisted_order_preferences"] = [
                *persisted_order_preferences,
                order_preference.value.casefold(),
            ]
        result.remembered_memories = [
            MemoryCandidate(kind=memory.kind, value=memory.value)
            for memory in self._store.list_memories(actor_id)
        ]
