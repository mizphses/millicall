/**
 * TanStack Query キー一元管理。
 *
 * 一覧ページ（CRUD）と Dashboard（集計 / 最新 N 件）を別名前空間に分離し、
 * キャッシュの型衝突（number vs 配列）を防ぐ。
 *
 * 各ページはここからキーをインポートし、ローカル定義を持たない。
 */

// ─────────────────────────────────────────────────────────
// 一覧ページ用キー（API から配列を返す・ミューテーション後に invalidate する）
// ─────────────────────────────────────────────────────────
export const EXTENSIONS_KEY = ["extensions"] as const;
export const TRUNKS_KEY = ["trunks"] as const;
export const AGENTS_KEY = ["ai-agents"] as const;
export const PROVIDERS_KEY = ["providers"] as const;
export const ROUTES_KEY = ["routes"] as const;
export const CONTACTS_KEY = ["contacts"] as const;
export const CDR_KEY = ["cdr"] as const;
export const CALL_MESSAGES_KEY = ["call-messages"] as const;

// ─────────────────────────────────────────────────────────
// Dashboard 専用キー（number / 最新 N 件を返す。一覧キーと別トップレベル）
// ─────────────────────────────────────────────────────────
export const DASHBOARD_KEYS = {
  extensionsCount: ["dashboard", "extensions-count"] as const,
  trunksCount: ["dashboard", "trunks-count"] as const,
  aiAgentsCount: ["dashboard", "ai-agents-count"] as const,
  recentCdr: ["dashboard", "recent-cdr"] as const,
} as const;

// ─────────────────────────────────────────────────────────
// ワークフロー用キー（一覧 vs 個別を分離して型衝突を防ぐ）
// ─────────────────────────────────────────────────────────
export const WORKFLOWS_KEY = ["workflows"] as const;
/** 個別ワークフロー取得（ [workflows, id] でネームスペース分離）。 */
export const workflowKey = (id: number) => ["workflows", id] as const;
/** node-types カタログ（更新不要なので単独キー）。 */
export const WORKFLOW_NODE_TYPES_KEY = ["workflow-node-types"] as const;

// ─────────────────────────────────────────────────────────
// ネットワーク設定用キー（Phase 5 T4）
// ─────────────────────────────────────────────────────────

/** ネットワーク設定取得 / PUT 後の invalidate に使う。 */
export const NETWORK_CONFIG_KEY = ["network-config"] as const;
/** Tailscale ステータスポーリング用キー（config とは別トップレベル）。 */
export const NETWORK_TAILSCALE_STATUS_KEY = ["network-tailscale-status"] as const;
