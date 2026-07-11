"""Strict, typed workflow node models (Phase 4b Task 1).

Each of the 19 node types has a typed Pydantic ``config`` model. The 19 node
wrapper models are assembled into a discriminated union ``WorkflowNode`` keyed on
``type``. Config fields carry GUI metadata via ``json_schema_extra`` so a single
source of truth drives both validation *and* the node-types API served to the
xyflow editor (``node_type_catalog``).

Config field UI-type vocabulary (delivered to the frontend):
``string | textarea | number | select | multi_select | boolean | json |
key_value_list | provider_ref | agent_ref | workflow_ref``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


def _ui(
    label: str,
    ui_type: str,
    *,
    options: list[str] | None = None,
    provider_type: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"label": label, "ui_type": ui_type}
    if options is not None:
        meta["options"] = options
    if provider_type is not None:
        meta["provider_type"] = provider_type
    if description is not None:
        meta["description"] = description
    return meta


class _Config(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --------------------------------------------------------------------------- #
# common (8)
# --------------------------------------------------------------------------- #


class StartConfig(_Config):
    # le=20: dialplan の sleep(ring_count*6000ms) が無制限に伸びて DID を長時間
    # 応答前で塞ぐのを防ぐ（20 コール ≒ 2 分が実用上の上限）。
    ring_count: int = Field(
        default=0,
        ge=0,
        le=20,
        json_schema_extra=_ui("応答前コール数", "number", description="0=即応答"),
    )


class EmptyConfig(_Config):
    """Terminal nodes with no configuration (end, hangup)."""


class PlayAudioConfig(_Config):
    tts_text: str = Field(..., json_schema_extra=_ui("読み上げテキスト", "textarea"))
    tts_provider_id: int | None = Field(
        default=None, json_schema_extra=_ui("TTSプロバイダ", "provider_ref", provider_type="tts")
    )
    file_path: str = Field(default="", json_schema_extra=_ui("再生ファイル", "string"))


class TransferConfig(_Config):
    destination: str = Field(..., json_schema_extra=_ui("転送先", "string"))
    transfer_type: Literal["blind"] = Field(
        default="blind", json_schema_extra=_ui("転送種別", "select", options=["blind"])
    )


class ConditionConfig(_Config):
    variable: str = Field(..., json_schema_extra=_ui("変数名", "string"))
    operator: Literal["eq", "neq", "gt", "lt", "gte", "lte", "contains"] = Field(
        default="eq",
        json_schema_extra=_ui(
            "演算子", "select", options=["eq", "neq", "gt", "lt", "gte", "lte", "contains"]
        ),
    )
    value: str = Field(..., json_schema_extra=_ui("比較値", "string"))


class SetVariableConfig(_Config):
    variable: str = Field(..., json_schema_extra=_ui("変数名", "string"))
    value: str = Field(..., json_schema_extra=_ui("値（{{var}} 展開可）", "string"))


class GotoConfig(_Config):
    target_node_id: str = Field(..., json_schema_extra=_ui("ジャンプ先ノードID", "string"))


# --------------------------------------------------------------------------- #
# ivr (4)
# --------------------------------------------------------------------------- #


class DtmfInputConfig(_Config):
    prompt_mode: Literal["tts", "beep", "none"] = Field(
        default="tts",
        json_schema_extra=_ui("プロンプト方式", "select", options=["tts", "beep", "none"]),
    )
    prompt_text: str = Field(default="", json_schema_extra=_ui("プロンプト文", "textarea"))
    tts_provider_id: int | None = Field(
        default=None, json_schema_extra=_ui("TTSプロバイダ", "provider_ref", provider_type="tts")
    )
    max_digits: int = Field(default=1, ge=1, json_schema_extra=_ui("最大桁数", "number"))
    timeout: int = Field(default=5, ge=1, json_schema_extra=_ui("タイムアウト秒", "number"))
    terminator: str = Field(default="#", json_schema_extra=_ui("終端キー", "string"))
    variable: str = Field(default="dtmf_result", json_schema_extra=_ui("格納変数名", "string"))


class MenuConfig(_Config):
    prompt_mode: Literal["tts", "beep", "none"] = Field(
        default="tts",
        json_schema_extra=_ui("プロンプト方式", "select", options=["tts", "beep", "none"]),
    )
    prompt_text: str = Field(..., json_schema_extra=_ui("プロンプト文", "textarea"))
    tts_provider_id: int | None = Field(
        default=None, json_schema_extra=_ui("TTSプロバイダ", "provider_ref", provider_type="tts")
    )
    timeout: int = Field(default=5, ge=1, json_schema_extra=_ui("タイムアウト秒", "number"))
    max_retries: int = Field(default=3, ge=0, json_schema_extra=_ui("最大リトライ", "number"))
    invalid_text: str = Field(default="", json_schema_extra=_ui("エラー時文言", "string"))


class TimeConditionConfig(_Config):
    start_time: str = Field(default="09:00", json_schema_extra=_ui("開始時刻", "string"))
    end_time: str = Field(default="18:00", json_schema_extra=_ui("終了時刻", "string"))
    days_of_week: list[str] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"],
        json_schema_extra=_ui(
            "対象曜日",
            "multi_select",
            options=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        ),
    )
    timezone: str = Field(default="Asia/Tokyo", json_schema_extra=_ui("タイムゾーン", "string"))


class VoicemailConfig(_Config):
    mailbox: str = Field(..., json_schema_extra=_ui("メールボックス", "string"))
    greeting_text: str = Field(default="", json_schema_extra=_ui("応答メッセージ", "textarea"))
    tts_provider_id: int | None = Field(
        default=None, json_schema_extra=_ui("TTSプロバイダ", "provider_ref", provider_type="tts")
    )
    max_seconds: int = Field(default=120, ge=1, json_schema_extra=_ui("最大録音秒", "number"))


# --------------------------------------------------------------------------- #
# ai_workflow (6)
# --------------------------------------------------------------------------- #


class AiConversationConfig(_Config):
    agent_id: int | None = Field(default=None, json_schema_extra=_ui("AIエージェント", "agent_ref"))
    system_prompt_override: str | None = Field(
        default=None, json_schema_extra=_ui("システムプロンプト上書き", "textarea")
    )
    greeting_override: str = Field(default="", json_schema_extra=_ui("挨拶文上書き", "string"))
    max_turns: int = Field(default=10, ge=1, json_schema_extra=_ui("最大ターン数", "number"))
    extraction_mode: Literal["auto", "direct"] = Field(
        default="auto", json_schema_extra=_ui("抽出モード", "select", options=["auto", "direct"])
    )
    extract_variables: dict[str, str] = Field(
        default_factory=dict, json_schema_extra=_ui("抽出変数（変数名→説明）", "key_value_list")
    )

    @model_validator(mode="after")
    def _require_agent_or_override(self) -> AiConversationConfig:
        has_agent = self.agent_id is not None
        has_override = bool(self.system_prompt_override and self.system_prompt_override.strip())
        if not has_agent and not has_override:
            raise ValueError("ai_conversation requires either agent_id or system_prompt_override")
        return self


class IntentDetectionConfig(_Config):
    intents: dict[str, str] = Field(
        ..., min_length=1, json_schema_extra=_ui("意図（key→説明）", "key_value_list")
    )
    llm_provider_id: int = Field(
        ..., json_schema_extra=_ui("LLMプロバイダ", "provider_ref", provider_type="llm")
    )
    fallback_intent: str = Field(
        default="other", json_schema_extra=_ui("フォールバック意図", "string")
    )


class CollectInfoConfig(_Config):
    fields: dict[str, str] = Field(
        ..., min_length=1, json_schema_extra=_ui("収集項目（変数名→質問）", "key_value_list")
    )
    agent_id: int = Field(..., json_schema_extra=_ui("AIエージェント", "agent_ref"))
    tts_provider_id: int | None = Field(
        default=None, json_schema_extra=_ui("TTSプロバイダ", "provider_ref", provider_type="tts")
    )
    confirmation: bool = Field(default=True, json_schema_extra=_ui("確認する", "boolean"))


class ApiCallConfig(_Config):
    url: str = Field(..., json_schema_extra=_ui("URL（{{var}} 展開可）", "string"))
    method: Literal["GET", "POST", "PUT", "DELETE"] = Field(
        default="POST",
        json_schema_extra=_ui("メソッド", "select", options=["GET", "POST", "PUT", "DELETE"]),
    )
    headers: dict[str, str] = Field(default_factory=dict, json_schema_extra=_ui("ヘッダ", "json"))
    content_type: Literal["json", "form"] = Field(
        default="json", json_schema_extra=_ui("Content-Type", "select", options=["json", "form"])
    )
    body_template: str = Field(default="", json_schema_extra=_ui("ボディテンプレート", "textarea"))
    result_variable: str = Field(
        default="api_result", json_schema_extra=_ui("結果格納変数", "string")
    )
    timeout: int = Field(default=10, ge=1, json_schema_extra=_ui("タイムアウト秒", "number"))


class EmailNotifyConfig(_Config):
    to: str = Field(..., json_schema_extra=_ui("宛先", "string"))
    subject_template: str = Field(..., json_schema_extra=_ui("件名テンプレート", "string"))
    body_template: str = Field(..., json_schema_extra=_ui("本文テンプレート", "textarea"))


class HumanEscalationConfig(_Config):
    destination: str = Field(..., json_schema_extra=_ui("転送先", "string"))
    announcement_text: str = Field(default="", json_schema_extra=_ui("アナウンス文", "textarea"))
    tts_provider_id: int | None = Field(
        default=None, json_schema_extra=_ui("TTSプロバイダ", "provider_ref", provider_type="tts")
    )
    summary_to_agent: bool = Field(default=True, json_schema_extra=_ui("要約を渡す", "boolean"))


# --------------------------------------------------------------------------- #
# special (1)
# --------------------------------------------------------------------------- #


class CallWorkflowConfig(_Config):
    workflow_id: int = Field(..., json_schema_extra=_ui("呼び出すワークフロー", "workflow_ref"))


# --------------------------------------------------------------------------- #
# Node wrapper models
# --------------------------------------------------------------------------- #


class Position(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: float = 0.0
    y: float = 0.0


class _NodeBase(BaseModel):
    # tolerate GUI-only keys (width, height, selected, data, ...) on the wrapper
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    position: Position | None = None


class StartNode(_NodeBase):
    type: Literal["start"]
    config: StartConfig = Field(default_factory=StartConfig)


class EndNode(_NodeBase):
    type: Literal["end"]
    config: EmptyConfig = Field(default_factory=EmptyConfig)


class HangupNode(_NodeBase):
    type: Literal["hangup"]
    config: EmptyConfig = Field(default_factory=EmptyConfig)


class PlayAudioNode(_NodeBase):
    type: Literal["play_audio"]
    config: PlayAudioConfig = Field(default_factory=PlayAudioConfig)


class TransferNode(_NodeBase):
    type: Literal["transfer"]
    config: TransferConfig = Field(default_factory=TransferConfig)


class ConditionNode(_NodeBase):
    type: Literal["condition"]
    config: ConditionConfig = Field(default_factory=ConditionConfig)


class SetVariableNode(_NodeBase):
    type: Literal["set_variable"]
    config: SetVariableConfig = Field(default_factory=SetVariableConfig)


class GotoNode(_NodeBase):
    type: Literal["goto"]
    config: GotoConfig = Field(default_factory=GotoConfig)


class DtmfInputNode(_NodeBase):
    type: Literal["dtmf_input"]
    config: DtmfInputConfig = Field(default_factory=DtmfInputConfig)


class MenuNode(_NodeBase):
    type: Literal["menu"]
    config: MenuConfig = Field(default_factory=MenuConfig)


class TimeConditionNode(_NodeBase):
    type: Literal["time_condition"]
    config: TimeConditionConfig = Field(default_factory=TimeConditionConfig)


class VoicemailNode(_NodeBase):
    type: Literal["voicemail"]
    config: VoicemailConfig = Field(default_factory=VoicemailConfig)


class AiConversationNode(_NodeBase):
    type: Literal["ai_conversation"]
    config: AiConversationConfig = Field(default_factory=AiConversationConfig)


class IntentDetectionNode(_NodeBase):
    type: Literal["intent_detection"]
    config: IntentDetectionConfig = Field(default_factory=IntentDetectionConfig)


class CollectInfoNode(_NodeBase):
    type: Literal["collect_info"]
    config: CollectInfoConfig = Field(default_factory=CollectInfoConfig)


class ApiCallNode(_NodeBase):
    type: Literal["api_call"]
    config: ApiCallConfig = Field(default_factory=ApiCallConfig)


class EmailNotifyNode(_NodeBase):
    type: Literal["email_notify"]
    config: EmailNotifyConfig = Field(default_factory=EmailNotifyConfig)


class HumanEscalationNode(_NodeBase):
    type: Literal["human_escalation"]
    config: HumanEscalationConfig = Field(default_factory=HumanEscalationConfig)


class CallWorkflowNode(_NodeBase):
    type: Literal["call_workflow"]
    config: CallWorkflowConfig = Field(default_factory=CallWorkflowConfig)


WorkflowNode = Annotated[
    StartNode
    | EndNode
    | HangupNode
    | PlayAudioNode
    | TransferNode
    | ConditionNode
    | SetVariableNode
    | GotoNode
    | DtmfInputNode
    | MenuNode
    | TimeConditionNode
    | VoicemailNode
    | AiConversationNode
    | IntentDetectionNode
    | CollectInfoNode
    | ApiCallNode
    | EmailNotifyNode
    | HumanEscalationNode
    | CallWorkflowNode,
    Field(discriminator="type"),
]

_NODE_ADAPTER: TypeAdapter[Any] = TypeAdapter(WorkflowNode)


def parse_node(data: dict[str, Any]) -> Any:
    """Validate a single node dict into its typed model via the discriminator."""
    return _NODE_ADAPTER.validate_python(data)


# --------------------------------------------------------------------------- #
# Catalog metadata (type -> category, label, config model)
# --------------------------------------------------------------------------- #

# (type, category, label, config_model)
_NODE_SPECS: list[tuple[str, str, str, type[_Config]]] = [
    ("start", "common", "開始", StartConfig),
    ("end", "common", "終了", EmptyConfig),
    ("hangup", "common", "切断", EmptyConfig),
    ("play_audio", "common", "音声再生", PlayAudioConfig),
    ("transfer", "common", "転送", TransferConfig),
    ("condition", "common", "条件分岐", ConditionConfig),
    ("set_variable", "common", "変数設定", SetVariableConfig),
    ("goto", "common", "ジャンプ", GotoConfig),
    ("dtmf_input", "ivr", "DTMF入力", DtmfInputConfig),
    ("menu", "ivr", "メニュー", MenuConfig),
    ("time_condition", "ivr", "時間条件", TimeConditionConfig),
    ("voicemail", "ivr", "ボイスメール", VoicemailConfig),
    ("ai_conversation", "ai_workflow", "AI会話", AiConversationConfig),
    ("intent_detection", "ai_workflow", "意図検出", IntentDetectionConfig),
    ("collect_info", "ai_workflow", "情報収集", CollectInfoConfig),
    ("api_call", "ai_workflow", "API呼び出し", ApiCallConfig),
    ("email_notify", "ai_workflow", "メール通知", EmailNotifyConfig),
    ("human_escalation", "ai_workflow", "有人エスカレーション", HumanEscalationConfig),
    ("call_workflow", "special", "ワークフロー呼び出し", CallWorkflowConfig),
]

NODE_TYPES: list[str] = [spec[0] for spec in _NODE_SPECS]

CONFIG_MODELS: dict[str, type[_Config]] = {spec[0]: spec[3] for spec in _NODE_SPECS}


def _serialize_config_schema(model: type[_Config]) -> list[dict[str, Any]]:
    schema: list[dict[str, Any]] = []
    for key, field in model.model_fields.items():
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        entry: dict[str, Any] = {
            "key": key,
            "type": extra.get("ui_type", "string"),
            "label": extra.get("label", key),
            "required": field.is_required(),
        }
        if not field.is_required():
            entry["default"] = field.get_default(call_default_factory=True)
        if "options" in extra:
            entry["options"] = extra["options"]
        if "provider_type" in extra:
            entry["provider_type"] = extra["provider_type"]
        if "description" in extra:
            entry["description"] = extra["description"]
        schema.append(entry)
    return schema


def node_type_catalog() -> list[dict[str, Any]]:
    """Serialize the 19 node types (config schema + handles) for GUI delivery."""
    # imported here to avoid a circular import at module load time
    from millicall.workflows.handles import HANDLE_VOCAB

    catalog: list[dict[str, Any]] = []
    for node_type, category, label, config_model in _NODE_SPECS:
        catalog.append(
            {
                "type": node_type,
                "category": category,
                "label": label,
                "config_schema": _serialize_config_schema(config_model),
                "output_handles": list(HANDLE_VOCAB.get(node_type, [])),
                "dynamic_handles": node_type in ("dtmf_input", "intent_detection"),
            }
        )
    return catalog
