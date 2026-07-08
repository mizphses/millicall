"""Task 1: workflow strict schema + handle vocabulary + graph validation."""

import pytest
from pydantic import ValidationError

from millicall.workflows.errors import WorkflowExecutionError, WorkflowValidationError
from millicall.workflows.handles import HANDLE_VOCAB, output_handles
from millicall.workflows.nodes import (
    NODE_TYPES,
    WorkflowNode,
    node_type_catalog,
    parse_node,
)
from millicall.workflows.schema import (
    ValidationResult,
    WorkflowDefinition,
    validate_graph,
)

# --------------------------------------------------------------------------- #
# Node config models (valid / invalid)
# --------------------------------------------------------------------------- #


def test_start_config_defaults_ring_count_zero() -> None:
    node = parse_node({"id": "s", "type": "start"})
    assert node.type == "start"
    assert node.config.ring_count == 0


def test_start_config_ring_count_upper_bound() -> None:
    # le=20: 上限内は許可、超過は拒否（無制限 pre-answer sleep 防止）。
    assert (
        parse_node({"id": "s", "type": "start", "config": {"ring_count": 20}}).config.ring_count
        == 20
    )
    with pytest.raises(ValidationError):
        parse_node({"id": "s", "type": "start", "config": {"ring_count": 21}})


def test_play_audio_requires_tts_text() -> None:
    with pytest.raises(ValidationError):
        parse_node({"id": "p", "type": "play_audio", "config": {}})


def test_play_audio_valid() -> None:
    node = parse_node(
        {"id": "p", "type": "play_audio", "config": {"tts_text": "hello", "tts_provider_id": 3}}
    )
    assert node.config.tts_text == "hello"
    assert node.config.tts_provider_id == 3


def test_transfer_requires_destination() -> None:
    with pytest.raises(ValidationError):
        parse_node({"id": "t", "type": "transfer", "config": {"transfer_type": "blind"}})


def test_transfer_attended_rejected() -> None:
    # ruling 4: attended transfer is not supported this phase -> hard reject
    with pytest.raises(ValidationError):
        parse_node(
            {
                "id": "t",
                "type": "transfer",
                "config": {"destination": "100", "transfer_type": "attended"},
            }
        )


def test_transfer_blind_valid() -> None:
    node = parse_node(
        {"id": "t", "type": "transfer", "config": {"destination": "100", "transfer_type": "blind"}}
    )
    assert node.config.transfer_type == "blind"


def test_condition_operator_enumerated() -> None:
    with pytest.raises(ValidationError):
        parse_node(
            {
                "id": "c",
                "type": "condition",
                "config": {"variable": "x", "operator": "bogus", "value": "1"},
            }
        )


def test_ai_conversation_requires_agent_or_override() -> None:
    # ruling 8: agent_id OR system_prompt_override, both absent -> error
    with pytest.raises(ValidationError):
        parse_node({"id": "a", "type": "ai_conversation", "config": {}})


def test_ai_conversation_agent_only_valid() -> None:
    node = parse_node({"id": "a", "type": "ai_conversation", "config": {"agent_id": 5}})
    assert node.config.agent_id == 5


def test_ai_conversation_override_only_valid() -> None:
    node = parse_node(
        {"id": "a", "type": "ai_conversation", "config": {"system_prompt_override": "be nice"}}
    )
    assert node.config.system_prompt_override == "be nice"


def test_unknown_node_type_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_node({"id": "x", "type": "not_a_real_type", "config": {}})


def test_intent_detection_requires_intents_and_provider() -> None:
    with pytest.raises(ValidationError):
        parse_node({"id": "i", "type": "intent_detection", "config": {}})
    node = parse_node(
        {
            "id": "i",
            "type": "intent_detection",
            "config": {
                "intents": {"reservation": "予約", "support": "サポート"},
                "llm_provider_id": 2,
            },
        }
    )
    assert node.config.fallback_intent == "other"


def test_api_call_defaults() -> None:
    node = parse_node({"id": "api", "type": "api_call", "config": {"url": "https://x.test"}})
    assert node.config.method == "POST"
    assert node.config.result_variable == "api_result"
    assert node.config.timeout == 10


def test_empty_config_nodes() -> None:
    for t in ("end", "hangup"):
        node = parse_node({"id": t, "type": t})
        assert node.type == t


# --------------------------------------------------------------------------- #
# Discriminated union
# --------------------------------------------------------------------------- #


def test_definition_parses_mixed_nodes_into_typed_configs() -> None:
    defn = WorkflowDefinition.model_validate(
        {
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "play_audio", "config": {"tts_text": "hi"}},
                {"id": "e", "type": "end"},
            ],
            "edges": [],
        }
    )
    kinds = {n.id: n.type for n in defn.nodes}
    assert kinds == {"s": "start", "p": "play_audio", "e": "end"}
    play = next(n for n in defn.nodes if n.id == "p")
    assert play.config.tts_text == "hi"


def test_node_types_are_nineteen() -> None:
    assert len(NODE_TYPES) == 19


# --------------------------------------------------------------------------- #
# Handle vocabulary
# --------------------------------------------------------------------------- #


def test_static_handle_vocab() -> None:
    assert HANDLE_VOCAB["start"] == ["out"]
    assert HANDLE_VOCAB["end"] == []
    assert HANDLE_VOCAB["hangup"] == []
    assert HANDLE_VOCAB["transfer"] == []
    assert HANDLE_VOCAB["voicemail"] == []
    assert HANDLE_VOCAB["human_escalation"] == []
    assert HANDLE_VOCAB["goto"] == []
    assert HANDLE_VOCAB["condition"] == ["true", "false"]
    assert HANDLE_VOCAB["time_condition"] == ["match", "no_match"]
    assert HANDLE_VOCAB["api_call"] == ["success", "error"]
    assert HANDLE_VOCAB["email_notify"] == ["success", "error"]
    assert HANDLE_VOCAB["play_audio"] == ["out"]
    assert HANDLE_VOCAB["menu"] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "0",
        "timeout",
    ]


def test_output_handles_dtmf_single_digit() -> None:
    node = parse_node(
        {"id": "d", "type": "dtmf_input", "config": {"max_digits": 1, "variable": "x"}}
    )
    assert output_handles(node) == [
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "timeout",
    ]


def test_output_handles_dtmf_multi_digit() -> None:
    node = parse_node(
        {"id": "d", "type": "dtmf_input", "config": {"max_digits": 4, "variable": "x"}}
    )
    assert output_handles(node) == ["done", "timeout"]


def test_output_handles_intent_detection_dynamic() -> None:
    node = parse_node(
        {
            "id": "i",
            "type": "intent_detection",
            "config": {
                "intents": {"reservation": "a", "support": "b"},
                "llm_provider_id": 1,
                "fallback_intent": "other",
            },
        }
    )
    assert output_handles(node) == ["reservation", "support", "other"]


def test_output_handles_intent_detection_dedup_fallback() -> None:
    node = parse_node(
        {
            "id": "i",
            "type": "intent_detection",
            "config": {
                "intents": {"reservation": "a", "other": "b"},
                "llm_provider_id": 1,
                "fallback_intent": "other",
            },
        }
    )
    assert output_handles(node) == ["reservation", "other"]


def test_output_handles_menu() -> None:
    node = parse_node({"id": "m", "type": "menu", "config": {"prompt_text": "choose"}})
    assert output_handles(node) == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "0",
        "timeout",
    ]


# --------------------------------------------------------------------------- #
# validate_graph
# --------------------------------------------------------------------------- #


def _defn(nodes: list[dict], edges: list[dict]) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"nodes": nodes, "edges": edges})


def test_valid_graph_has_no_errors() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {"id": "p", "type": "play_audio", "config": {"tts_text": "hi"}},
            {"id": "e", "type": "end"},
        ],
        [
            {"id": "e1", "source": "s", "target": "p", "sourceHandle": "out"},
            {"id": "e2", "source": "p", "target": "e", "sourceHandle": "out"},
        ],
    )
    result = validate_graph(defn)
    assert isinstance(result, ValidationResult)
    assert result.errors == []
    assert result.warnings == []


def test_start_missing_is_error() -> None:
    defn = _defn([{"id": "e", "type": "end"}], [])
    result = validate_graph(defn)
    assert any("start" in e.lower() for e in result.errors)


def test_two_starts_is_error() -> None:
    defn = _defn(
        [
            {"id": "s1", "type": "start"},
            {"id": "s2", "type": "start"},
            {"id": "e", "type": "end"},
        ],
        [{"id": "x", "source": "s1", "target": "e", "sourceHandle": "out"}],
    )
    result = validate_graph(defn)
    assert any("start" in e.lower() for e in result.errors)


def test_edge_dangling_source_is_error() -> None:
    defn = _defn(
        [{"id": "s", "type": "start"}, {"id": "e", "type": "end"}],
        [{"id": "x", "source": "ghost", "target": "e", "sourceHandle": "out"}],
    )
    result = validate_graph(defn)
    assert any("ghost" in e for e in result.errors)


def test_edge_dangling_target_is_error() -> None:
    defn = _defn(
        [{"id": "s", "type": "start"}, {"id": "e", "type": "end"}],
        [{"id": "x", "source": "s", "target": "ghost", "sourceHandle": "out"}],
    )
    result = validate_graph(defn)
    assert any("ghost" in e for e in result.errors)


def test_unknown_source_handle_is_error() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {
                "id": "c",
                "type": "condition",
                "config": {"variable": "x", "operator": "eq", "value": "1"},
            },
            {"id": "e", "type": "end"},
        ],
        [
            {"id": "e1", "source": "s", "target": "c", "sourceHandle": "out"},
            {"id": "e2", "source": "c", "target": "e", "sourceHandle": "maybe"},
        ],
    )
    result = validate_graph(defn)
    assert any("maybe" in e for e in result.errors)


def test_edge_from_terminal_node_is_error() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {"id": "e", "type": "end"},
        ],
        [
            {"id": "e1", "source": "s", "target": "e", "sourceHandle": "out"},
            {"id": "e2", "source": "e", "target": "s", "sourceHandle": "out"},
        ],
    )
    result = validate_graph(defn)
    assert any("e2" in e or "end" in e.lower() for e in result.errors)


def test_intent_detection_dynamic_handle_accepted() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {
                "id": "i",
                "type": "intent_detection",
                "config": {"intents": {"foo": "d"}, "llm_provider_id": 1},
            },
            {"id": "e", "type": "end"},
        ],
        [
            {"id": "e1", "source": "s", "target": "i", "sourceHandle": "out"},
            {"id": "e2", "source": "i", "target": "e", "sourceHandle": "foo"},
        ],
    )
    result = validate_graph(defn)
    assert result.errors == []


def test_goto_target_missing_is_error() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {"id": "g", "type": "goto", "config": {"target_node_id": "ghost"}},
        ],
        [{"id": "e1", "source": "s", "target": "g", "sourceHandle": "out"}],
    )
    result = validate_graph(defn)
    assert any("ghost" in e for e in result.errors)


def test_goto_cycle_is_error() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {"id": "g1", "type": "goto", "config": {"target_node_id": "g2"}},
            {"id": "g2", "type": "goto", "config": {"target_node_id": "g1"}},
        ],
        [
            {"id": "e1", "source": "s", "target": "g1", "sourceHandle": "out"},
        ],
    )
    result = validate_graph(defn)
    assert any("cycle" in e.lower() or "循環" in e for e in result.errors)


def test_call_workflow_self_reference_is_error() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {"id": "c", "type": "call_workflow", "config": {"workflow_id": 42}},
            {"id": "e", "type": "end"},
        ],
        [
            {"id": "e1", "source": "s", "target": "c", "sourceHandle": "out"},
            {"id": "e2", "source": "c", "target": "e", "sourceHandle": "out"},
        ],
    )
    result = validate_graph(defn, workflow_id=42)
    assert any("42" in e or "self" in e.lower() or "循環" in e for e in result.errors)


def test_unreachable_node_is_warning_not_error() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {"id": "e", "type": "end"},
            {"id": "orphan", "type": "play_audio", "config": {"tts_text": "hi"}},
        ],
        [{"id": "e1", "source": "s", "target": "e", "sourceHandle": "out"}],
    )
    result = validate_graph(defn)
    assert result.errors == []
    assert any("orphan" in w for w in result.warnings)


def test_reachability_follows_goto_target() -> None:
    defn = _defn(
        [
            {"id": "s", "type": "start"},
            {"id": "g", "type": "goto", "config": {"target_node_id": "p"}},
            {"id": "p", "type": "play_audio", "config": {"tts_text": "hi"}},
            {"id": "e", "type": "end"},
        ],
        [
            {"id": "e1", "source": "s", "target": "g", "sourceHandle": "out"},
            {"id": "e2", "source": "p", "target": "e", "sourceHandle": "out"},
        ],
    )
    result = validate_graph(defn)
    assert result.warnings == []


# --------------------------------------------------------------------------- #
# node-type serialization (for GUI delivery)
# --------------------------------------------------------------------------- #


def test_node_type_catalog_has_all_types_with_fields() -> None:
    catalog = node_type_catalog()
    assert len(catalog) == 19
    by_type = {c["type"]: c for c in catalog}
    play = by_type["play_audio"]
    assert play["category"] == "common"
    fields = {f["key"]: f for f in play["config_schema"]}
    assert fields["tts_text"]["required"] is True
    assert fields["tts_text"]["type"] == "textarea"
    assert fields["tts_provider_id"]["type"] == "provider_ref"
    assert fields["tts_provider_id"]["required"] is False
    # transfer only offers blind (attended removed per ruling 4)
    transfer = by_type["transfer"]
    tf = {f["key"]: f for f in transfer["config_schema"]}
    assert tf["transfer_type"]["options"] == ["blind"]


def test_node_type_catalog_output_handles_present() -> None:
    catalog = node_type_catalog()
    by_type = {c["type"]: c for c in catalog}
    assert by_type["condition"]["output_handles"] == ["true", "false"]
    assert by_type["start"]["output_handles"] == ["out"]


# --------------------------------------------------------------------------- #
# errors module
# --------------------------------------------------------------------------- #


def test_error_types_are_exceptions() -> None:
    assert issubclass(WorkflowValidationError, Exception)
    assert issubclass(WorkflowExecutionError, Exception)


def test_workflow_node_type_alias_exists() -> None:
    # WorkflowNode is the discriminated-union alias used by downstream tasks
    assert WorkflowNode is not None
