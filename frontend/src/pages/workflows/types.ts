/**
 * ワークフローエディタ内部型定義（Phase 4b Task 10）。
 */

/** バックエンドの node-types API レスポンスの要素。 */
export interface NodeTypeInfo {
  type: string;
  category: string;
  label: string;
  config_schema: ConfigSchemaField[];
  output_handles: string[];
  dynamic_handles: boolean;
}

/** config_schema 1 フィールドの記述。 */
export interface ConfigSchemaField {
  key: string;
  type:
    | "string"
    | "textarea"
    | "number"
    | "select"
    | "boolean"
    | "provider_ref"
    | "agent_ref"
    | "key_value_list"
    | "multi_select"
    | "json";
  label: string;
  required: boolean;
  default?: unknown;
  options?: string[];
  provider_type?: string;
  description?: string;
}

/** xyflow ノードに格納する data ペイロード。 */
export interface WorkflowNodeData extends Record<string, unknown> {
  /** バックエンドのノード type（"start", "play_audio" …）。 */
  nodeType: string;
  /** 日本語ラベル。 */
  label: string;
  /** ノードの config（キー→値）。 */
  config: Record<string, unknown>;
  /** config_schema（カスタムノードが参照）。 */
  configSchema: ConfigSchemaField[];
  /** 静的 output_handles。 */
  outputHandles: string[];
  /** 動的ハンドル（dtmf_input / intent_detection）。 */
  dynamicHandles: boolean;
}

/** プロバイダ参照用。 */
export interface ProviderOption {
  id: number;
  name: string;
  type: string;
}

/** AI エージェント参照用。 */
export interface AgentOption {
  id: number;
  name: string;
}
