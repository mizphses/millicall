import type { components } from "../../api/schema";

export type ProviderRead = components["schemas"]["ProviderRead"];
export type ProviderCreate = components["schemas"]["ProviderCreate"];
export type ProviderUpdate = components["schemas"]["ProviderUpdate"];
export type ProviderKind = components["schemas"]["ProviderKind"];
export type ProviderType = components["schemas"]["ProviderType"];

/**
 * kind → config フォームのフィールド定義。
 *
 * 正典は backend の `src/millicall/ai/registry.py` の `build_llm` / `build_tts`
 * / `build_stt` が `config.get(...)` で読むキーとそのデフォルト値。
 * placeholder にはそのサーバ側デフォルトを出す（空入力＝デフォルト採用 = omit-if-empty）。
 * `valueType` は数値/文字列の別で、payload 変換時に number へパースする。
 */
export interface ConfigFieldDef {
  key: string;
  label: string;
  valueType: "string" | "number";
  placeholder?: string;
}

export interface KindDef {
  kind: ProviderKind;
  /** kind から一意に決まる provider type（KIND_BY_TYPE の逆写像）。 */
  type: ProviderType;
  /** UI 表示名。 */
  label: string;
  /** カード用の短い説明。 */
  description: string;
  /** api_key を使う kind か（registry が api_key を渡す kind のみ true）。 */
  usesApiKey: boolean;
  fields: ConfigFieldDef[];
}

/** type ごとの日本語ラベル。 */
export const TYPE_LABEL: Record<ProviderType, string> = {
  llm: "LLM",
  tts: "TTS",
  stt: "STT",
};

/**
 * kind → 定義マップ。フィールド定義は registry の cfg.get キーに一致させる。
 * - openai_compatible: base_url / model / temperature / max_tokens（api_key あり）
 * - anthropic: model / max_tokens（api_key あり）
 * - gemini: model / temperature（api_key あり）
 * - voicevox: engine_url / speaker（api_key なし）
 * - openjtalk: dict_dir / voice_path（api_key なし・サーバ側パス）
 * - whisper: model / language（api_key あり）
 * - google_stt: project / location / language / model（api_key なし・ADC 認証）
 */
export const KIND_CATALOG: Record<ProviderKind, KindDef> = {
  openai_compatible: {
    kind: "openai_compatible",
    type: "llm",
    label: "OpenAI 互換",
    description: "OpenAI / 互換エンドポイントのチャット補完",
    usesApiKey: true,
    fields: [
      { key: "base_url", label: "ベース URL", valueType: "string", placeholder: "https://api.openai.com/v1" },
      { key: "model", label: "モデル", valueType: "string", placeholder: "gpt-4o-mini" },
      { key: "temperature", label: "温度", valueType: "number", placeholder: "0.7" },
      { key: "max_tokens", label: "最大トークン", valueType: "number", placeholder: "500" },
    ],
  },
  anthropic: {
    kind: "anthropic",
    type: "llm",
    label: "Anthropic",
    description: "Claude メッセージ API",
    usesApiKey: true,
    fields: [
      { key: "model", label: "モデル", valueType: "string", placeholder: "claude-sonnet-4-20250514" },
      { key: "max_tokens", label: "最大トークン", valueType: "number", placeholder: "500" },
    ],
  },
  gemini: {
    kind: "gemini",
    type: "llm",
    label: "Gemini",
    description: "Google Gemini 生成 API",
    usesApiKey: true,
    fields: [
      { key: "model", label: "モデル", valueType: "string", placeholder: "gemini-2.5-flash" },
      { key: "temperature", label: "温度", valueType: "number", placeholder: "0.7" },
    ],
  },
  voicevox: {
    kind: "voicevox",
    type: "tts",
    label: "VOICEVOX",
    description: "VOICEVOX エンジンによる音声合成",
    usesApiKey: false,
    fields: [
      { key: "engine_url", label: "エンジン URL", valueType: "string", placeholder: "http://127.0.0.1:50021" },
      { key: "speaker", label: "話者 ID", valueType: "number", placeholder: "1" },
    ],
  },
  openjtalk: {
    kind: "openjtalk",
    type: "tts",
    label: "Open JTalk",
    description: "ローカル Open JTalk による音声合成",
    usesApiKey: false,
    fields: [
      {
        key: "dict_dir",
        label: "辞書ディレクトリ",
        valueType: "string",
        placeholder: "/var/lib/mecab/dic/open-jtalk/naist-jdic",
      },
      {
        key: "voice_path",
        label: "音声モデルパス",
        valueType: "string",
        placeholder: "/usr/share/hts-voice/.../nitech_jp_atr503_m001.htsvoice",
      },
    ],
  },
  whisper: {
    kind: "whisper",
    type: "stt",
    label: "Whisper",
    description: "OpenAI Whisper 音声認識",
    usesApiKey: true,
    fields: [
      { key: "model", label: "モデル", valueType: "string", placeholder: "whisper-1" },
      { key: "language", label: "言語", valueType: "string", placeholder: "ja" },
    ],
  },
  google_stt: {
    kind: "google_stt",
    type: "stt",
    label: "Google STT",
    description: "Google Cloud Speech-to-Text（ADC 認証）",
    usesApiKey: false,
    fields: [
      { key: "project", label: "プロジェクト", valueType: "string", placeholder: "my-gcp-project" },
      { key: "location", label: "ロケーション", valueType: "string", placeholder: "global" },
      { key: "language", label: "言語", valueType: "string", placeholder: "ja-JP" },
      { key: "model", label: "モデル", valueType: "string", placeholder: "chirp_2" },
    ],
  },
};

/** カタログの表示順（type ごとにグルーピング）。 */
export const KIND_ORDER: ProviderKind[] = [
  "openai_compatible",
  "anthropic",
  "gemini",
  "voicevox",
  "openjtalk",
  "whisper",
  "google_stt",
];

/** kind → type の逆写像（フォーム/payload で type を導出する唯一の経路）。 */
export function typeForKind(kind: ProviderKind): ProviderType {
  return KIND_CATALOG[kind].type;
}

/** SlidePanel フォームが保持する値。config は全フィールドを文字列で持つ。 */
export interface ProviderFormValues {
  name: string;
  kind: ProviderKind;
  /** 書き込み専用。作成時任意 / 編集時は空なら送らない（据え置き）。 */
  api_key: string;
  enabled: boolean;
  /** config フィールド key → 入力文字列。 */
  config: Record<string, string>;
}

export interface ProviderFormErrors {
  name?: string;
  /** config フィールド key → エラーメッセージ。 */
  config: Record<string, string>;
}

/** 指定 kind の config を空文字で初期化した Record を作る。 */
function emptyConfig(kind: ProviderKind): Record<string, string> {
  const config: Record<string, string> = {};
  for (const field of KIND_CATALOG[kind].fields) {
    config[field.key] = "";
  }
  return config;
}

/** 作成フォームの初期値（既定 kind は openai_compatible）。 */
export function emptyForm(kind: ProviderKind = "openai_compatible"): ProviderFormValues {
  return {
    name: "",
    kind,
    api_key: "",
    enabled: true,
    config: emptyConfig(kind),
  };
}

/** kind を切り替える（config は新 kind のキーで作り直し、既存値は同名キーのみ引き継ぐ）。 */
export function withKind(form: ProviderFormValues, kind: ProviderKind): ProviderFormValues {
  const next = emptyConfig(kind);
  for (const key of Object.keys(next)) {
    if (form.config[key] !== undefined) next[key] = form.config[key];
  }
  return { ...form, kind, config: next };
}

/** 既存プロバイダを編集フォーム値へ写像する。api_key は読み出せないため空にする。 */
export function formFromProvider(p: ProviderRead): ProviderFormValues {
  const kind = p.kind as ProviderKind;
  const config = emptyConfig(kind);
  for (const field of KIND_CATALOG[kind].fields) {
    const raw = p.config[field.key];
    if (raw !== undefined && raw !== null) config[field.key] = String(raw);
  }
  return {
    name: p.name,
    kind,
    api_key: "", // write-only: サーバから取得不可
    enabled: p.enabled,
    config,
  };
}

/**
 * フォームの config（文字列 Record）→ API config dict へ変換。
 * omit-if-empty: 空文字のフィールドは含めない（サーバ側デフォルトに委ねる）。
 * number フィールドは Number へパースして数値で送る。
 */
export function buildConfig(form: ProviderFormValues): Record<string, string | number> {
  const config: Record<string, string | number> = {};
  for (const field of KIND_CATALOG[form.kind].fields) {
    const raw = (form.config[field.key] ?? "").trim();
    if (raw === "") continue;
    if (field.valueType === "number") {
      config[field.key] = Number(raw);
    } else {
      config[field.key] = raw;
    }
  }
  return config;
}

/** 作成 payload への変換。type は kind から導出。api_key は非空のときのみ含める。 */
export function buildCreatePayload(form: ProviderFormValues): ProviderCreate {
  const payload: ProviderCreate = {
    name: form.name.trim(),
    type: typeForKind(form.kind),
    kind: form.kind,
    config: buildConfig(form),
    enabled: form.enabled,
  };
  if (form.api_key !== "") payload.api_key = form.api_key;
  return payload;
}

/**
 * 編集フォーム → PATCH payload 変換。
 * - name: 空か unchanged なら含めない
 * - config: 再構築した dict が original.config と異なれば含める
 * - api_key: 書き込み専用。空なら据え置き（含めない）、非空なら含める
 * - enabled: boolean 比較
 * kind / type は API 側で更新不可のため送らない。
 */
export function buildUpdatePayload(
  form: ProviderFormValues,
  original: ProviderRead,
): ProviderUpdate {
  const payload: ProviderUpdate = {};

  const name = form.name.trim();
  if (name !== "" && name !== original.name) payload.name = name;

  const config = buildConfig(form);
  if (!shallowConfigEqual(config, original.config)) payload.config = config;

  if (form.api_key !== "") payload.api_key = form.api_key;

  if (form.enabled !== original.enabled) payload.enabled = form.enabled;

  return payload;
}

/** config dict の等価判定（キー集合 + 値の緩い比較）。 */
function shallowConfigEqual(
  a: Record<string, string | number>,
  b: Record<string, unknown>,
): boolean {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    if (!(key in b)) return false;
    // 数値と文字列の混在を避けるため String 比較で緩く判定する。
    if (String(a[key]) !== String(b[key])) return false;
  }
  return true;
}

const NAME_MAX = 100;

/** クライアント側バリデーション。number フィールドは非空なら数値であることを検証。 */
export function validateForm(
  form: ProviderFormValues,
  mode: "create" | "edit",
): ProviderFormErrors {
  const errors: ProviderFormErrors = { config: {} };

  const name = form.name.trim();
  if (mode === "create") {
    if (name.length < 1) errors.name = "名前を入力してください";
  }
  if (name.length > NAME_MAX) {
    errors.name = `名前は ${NAME_MAX} 文字以内で入力してください`;
  }

  for (const field of KIND_CATALOG[form.kind].fields) {
    if (field.valueType !== "number") continue;
    const raw = (form.config[field.key] ?? "").trim();
    if (raw === "") continue;
    if (!Number.isFinite(Number(raw))) {
      errors.config[field.key] = `${field.label}は数値で入力してください`;
    }
  }

  return errors;
}

/** validateForm の結果にエラーがあるか。 */
export function hasErrors(errors: ProviderFormErrors): boolean {
  return errors.name !== undefined || Object.keys(errors.config).length > 0;
}
