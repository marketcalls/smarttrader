"""Repairs for LiteLLM's Responses-to-chat stream bridge, which every `chatgpt/` model goes through.

Missing tool arguments: when the ChatGPT backend makes tool calls, it often
sends no `function_call_arguments.delta` events; each call's complete arguments
arrive only in `response.function_call_arguments.done` and again in
`response.output_item.done`. LiteLLM 1.104 builds the chat stream from the
`output_item.added` announcement (id and name, empty arguments) plus the deltas
and ignores both `done` events, so Agno assembles `arguments: ""` and every call
fails with "Missing required argument". The same fault is documented and patched
in OpenAlgo (services/agent/litellm_tool_args.py).

The fix wraps `chunk_parser` per stream: it remembers which tool items already
streamed argument text, and when a `done` event arrives for one that streamed
none, it emits the complete arguments once at the tool-call index the bridge
assigned. Items that did stream are left untouched, so nothing is delivered
twice. If LiteLLM's bridge changes shape the patch steps aside.
"""

import logging
import threading
import warnings
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

_TOOL_ITEMS = frozenset({"function_call", "custom_tool_call"})
_ADDED = "response.output_item.added"
_ITEM_DONE = "response.output_item.done"
_ARG_DELTAS = frozenset({"response.function_call_arguments.delta", "response.custom_tool_call_input.delta"})
_ARGS_DONE = "response.function_call_arguments.done"
_INPUT_DONE = "response.custom_tool_call_input.done"
_STATE = "_smarttrader_tool_args"
_MARKER = "_smarttrader_patched"

_lock = threading.Lock()
_installed = False


def _event(chunk: Mapping[str, Any]) -> str:
    value = chunk.get("type")
    return str(getattr(value, "value", value) or "")


def _item_args(item: Any) -> str:
    if not isinstance(item, Mapping) or item.get("type") not in _TOOL_ITEMS:
        return ""
    value = item.get("arguments") or item.get("input")
    return value if isinstance(value, str) else ""


def recover(state: dict[str, set[int]], chunk: Any) -> tuple[int, str, Mapping[str, Any] | None] | None:
    """Return (output_index, arguments, missed_item) when this event carries arguments not yet streamed."""
    if not isinstance(chunk, Mapping):
        return None
    kind = _event(chunk)
    index = chunk.get("output_index", 0)
    index = index if isinstance(index, int) else 0
    announced, streamed = state.setdefault("announced", set()), state.setdefault("streamed", set())

    if kind == _ADDED:
        item = chunk.get("item")
        if isinstance(item, Mapping) and item.get("type") in _TOOL_ITEMS:
            announced.add(index)
            if _item_args(item):
                streamed.add(index)
        return None
    if kind in _ARG_DELTAS:
        if chunk.get("delta"):
            streamed.add(index)
        return None

    item: Mapping[str, Any] | None = None
    if kind == _ARGS_DONE:
        arguments = chunk.get("arguments")
    elif kind == _INPUT_DONE:
        arguments = chunk.get("input")
    elif kind == _ITEM_DONE:
        candidate = chunk.get("item")
        arguments = _item_args(candidate)
        item = candidate if isinstance(candidate, Mapping) else None
    else:
        return None
    if not isinstance(arguments, str) or not arguments or index in streamed:
        return None
    streamed.add(index)
    missed = item if item is not None and index not in announced else None
    announced.add(index)
    return index, arguments, missed


def _emit(iterator: Any, iterator_cls: Any, index: int, arguments: str, missed: Mapping[str, Any] | None) -> Any:
    from litellm.types.llms.openai import ChatCompletionToolCallFunctionChunk
    from litellm.types.utils import ChatCompletionToolCallChunk, Delta, ModelResponseStream, StreamingChoices

    tool_index = iterator_cls._sequential_tool_call_index(iterator._tool_call_index_map, index)
    call_id = (missed.get("call_id") or missed.get("id")) if missed else None
    name = missed.get("name") if missed else None
    chunk = ModelResponseStream(
        choices=[
            StreamingChoices(
                index=0,
                delta=Delta(
                    tool_calls=[
                        ChatCompletionToolCallChunk(
                            id=call_id,
                            index=tool_index,
                            type="function",
                            function=ChatCompletionToolCallFunctionChunk(name=name, arguments=arguments),
                        )
                    ]
                ),
                finish_reason=None,
            )
        ]
    )
    scope = getattr(iterator, "_with_stream_scoped_id", None)
    return scope(chunk) if callable(scope) else chunk


def install() -> bool:
    """Patch the stream bridge once. Returns True when the repair is in place."""
    global _installed
    with _lock:
        if _installed:
            return True
        # LiteLLM's usage object for the Responses bridge trips a harmless pydantic serializer warning per call.
        warnings.filterwarnings("ignore", message="Pydantic serializer warnings", category=UserWarning)
        try:
            from litellm.completion_extras.litellm_responses_transformation import transformation

            cls = transformation.OpenAiResponsesToChatCompletionStreamIterator
            original = cls.chunk_parser
            if getattr(original, _MARKER, False):
                _installed = True
                return True
            if not hasattr(cls, "_sequential_tool_call_index"):
                logger.warning("LiteLLM's stream bridge changed shape; tool-call argument recovery is off")
                return False

            def chunk_parser(self: Any, chunk: Any) -> Any:
                try:
                    state = self.__dict__.setdefault(_STATE, {})
                    found = recover(state, chunk)
                    if found is not None and isinstance(getattr(self, "_tool_call_index_map", None), dict):
                        return _emit(self, cls, *found)
                except Exception:  # noqa: BLE001 - fall back to LiteLLM's own handling
                    logger.exception("Could not recover streamed tool-call arguments")
                return original(self, chunk)

            setattr(chunk_parser, _MARKER, True)
            chunk_parser.__wrapped__ = original  # type: ignore[attr-defined]
            cls.chunk_parser = chunk_parser
        except Exception:  # noqa: BLE001
            logger.exception("Could not install the tool-call argument recovery")
            return False
        _installed = True
        return True
