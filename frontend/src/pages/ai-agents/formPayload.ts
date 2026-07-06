import type { components } from "../../api/schema";

export type AiAgentRead = components["schemas"]["AiAgentRead"];
export type AiAgentCreate = components["schemas"]["AiAgentCreate"];
export type AiAgentUpdate = components["schemas"]["AiAgentUpdate"];
export type ProviderRead = components["schemas"]["ProviderRead"];

/** SlidePanel フォームが保持する値。API 型とは分離し、UI 都合の型に寄せる。 */
export interface AiAgentFormValues {
  name: string;
  system_prompt: string;
  greeting: string;
  /** null = 未選択 */
  llm_provider_id: number | null;
  /** null = 未選択 */
  tts_provider_id: number | null;
  /** null = 未選択 */
  stt_provider_id: number | null;
  max_history: number;
  silence_end_ms: number;
  enabled: boolean;
}

/** 作成フォームの初期値。 */
export function emptyForm(): AiAgentFormValues {
  return {
    name: "",
    system_prompt: "",
    greeting: "",
    llm_provider_id: null,
    tts_provider_id: null,
    stt_provider_id: null,
    max_history: 10,
    silence_end_ms: 600,
    enabled: true,
  };
}

/** 既存エージェントを編集フォーム値へ写像する。 */
export function formFromAgent(agent: AiAgentRead): AiAgentFormValues {
  return {
    name: agent.name,
    system_prompt: agent.system_prompt,
    greeting: agent.greeting,
    llm_provider_id: agent.llm_provider_id,
    tts_provider_id: agent.tts_provider_id,
    stt_provider_id: agent.stt_provider_id,
    max_history: agent.max_history,
    silence_end_ms: agent.silence_end_ms,
    enabled: agent.enabled,
  };
}

/**
 * 作成 payload への変換。
 * 呼び出し前に validateForm で null チェック済みであることを前提とする。
 */
export function buildCreatePayload(form: AiAgentFormValues): AiAgentCreate {
  return {
    name: form.name.trim(),
    system_prompt: form.system_prompt,
    greeting: form.greeting,
    llm_provider_id: form.llm_provider_id as number,
    tts_provider_id: form.tts_provider_id as number,
    stt_provider_id: form.stt_provider_id as number,
    max_history: form.max_history,
    silence_end_ms: form.silence_end_ms,
    enabled: form.enabled,
  };
}

/**
 * 編集フォーム → PATCH payload への変換。
 * 「変更のないフィールドは payload に含めない（omit-if-unchanged）」を実装する。
 * name は空文字なら「据え置き」として送らない（omit-if-empty）。
 * system_prompt / greeting は "" も有効値なので変更があれば含める（omit-if-unchanged のみ）。
 */
export function buildUpdatePayload(
  form: AiAgentFormValues,
  original: AiAgentRead,
): AiAgentUpdate {
  const payload: AiAgentUpdate = {};

  const name = form.name.trim();
  if (name !== "" && name !== original.name) payload.name = name;

  if (form.system_prompt !== original.system_prompt) payload.system_prompt = form.system_prompt;
  if (form.greeting !== original.greeting) payload.greeting = form.greeting;

  if (form.llm_provider_id !== null && form.llm_provider_id !== original.llm_provider_id) {
    payload.llm_provider_id = form.llm_provider_id;
  }
  if (form.tts_provider_id !== null && form.tts_provider_id !== original.tts_provider_id) {
    payload.tts_provider_id = form.tts_provider_id;
  }
  if (form.stt_provider_id !== null && form.stt_provider_id !== original.stt_provider_id) {
    payload.stt_provider_id = form.stt_provider_id;
  }

  if (form.max_history !== original.max_history) payload.max_history = form.max_history;
  if (form.silence_end_ms !== original.silence_end_ms) payload.silence_end_ms = form.silence_end_ms;
  if (form.enabled !== original.enabled) payload.enabled = form.enabled;

  return payload;
}

/** クライアント側の軽いバリデーション。フィールド名 → エラーメッセージ。 */
export function validateForm(
  form: AiAgentFormValues,
  _mode: "create" | "edit",
): Partial<Record<keyof AiAgentFormValues, string>> {
  const errors: Partial<Record<keyof AiAgentFormValues, string>> = {};

  const name = form.name.trim();
  if (name.length < 1 || name.length > 100) {
    errors.name = "名前は 1〜100 文字で入力してください";
  }
  if (form.llm_provider_id === null) {
    errors.llm_provider_id = "LLM プロバイダを選択してください";
  }
  if (form.tts_provider_id === null) {
    errors.tts_provider_id = "TTS プロバイダを選択してください";
  }
  if (form.stt_provider_id === null) {
    errors.stt_provider_id = "STT プロバイダを選択してください";
  }
  if (!Number.isInteger(form.max_history) || form.max_history < 1 || form.max_history > 50) {
    errors.max_history = "最大履歴は 1〜50 の整数で入力してください";
  }
  if (
    !Number.isInteger(form.silence_end_ms) ||
    form.silence_end_ms < 200 ||
    form.silence_end_ms > 3000
  ) {
    errors.silence_end_ms = "無音検知は 200〜3000 ms の整数で入力してください";
  }

  return errors;
}
