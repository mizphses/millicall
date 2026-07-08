# millicall v2 ドキュメント

millicall は **フレッツ光電話（NTT HGW）に特化したローカル PBX** です。FreeSWITCH + FastAPI コア + Vite/React SPA を Docker Compose で構成し、ワンライナーでセットアップできます。

## アーキテクチャ概要

```
[ HGW / フレッツ光 ]
        │  SIP (UDP 5060/5080)
        ▼
┌──────────────────────────────────┐  host network
│  freeswitch コンテナ              │  (ghcr.io/mizphses/millicall-freeswitch)
│  FreeSWITCH + mod_audio_stream   │
└───────────────┬──────────────────┘
                │ ESL (127.0.0.1:8021) + WS 音声フォーク
┌───────────────▼──────────────────┐  host network
│  core コンテナ                    │  (ghcr.io/mizphses/millicall-core)
│  FastAPI (ポート 80)              │
│  SPA (Vite/React) 同梱           │
└──────┬──────────────┬────────────┘
       │ UNIX socket  │ HTTP (127.0.0.1:2375)
┌──────▼──────┐ ┌─────▼────────────────┐
│  netd       │ │  docker-proxy         │
│  dnsmasq    │ │  (Tecnativa socket    │
│  nftables   │ │   proxy: CONTAINERS / │
│  Tailscale  │ │   POST / INFO /       │
└─────────────┘ │   VERSION のみ許可)   │
                └──────────────────────┘
```

- **core**: FastAPI アプリ + 管理 SPA。SQLite DB (`data/millicall.db`)、FreeSWITCH 設定生成、AI パイプライン、MCP サーバー、ワークフローエンジンを担う。
- **freeswitch**: `mod_audio_stream` 同梱の FreeSWITCH。core が healthy になってから起動（`depends_on: service_healthy`）。設定ファイルは `data/freeswitch/` を bind mount。
- **netd**: `host` ネットワーク + `NET_ADMIN`/`NET_RAW` の特権コンテナ。dnsmasq（DHCP/DNS）・nftables（NAT/ACL）・Tailscale を管理する。core とは UNIX ソケット（`/run/millicall/netd.sock`）経由で通信。
- **docker-proxy**: `ghcr.io/tecnativa/docker-socket-proxy`。core が docker.sock に直接触れないよう最小 API のみ中継する。

## 機能一覧

| カテゴリ | 機能 |
|---|---|
| 基本通話 | 内線（SIP 登録/Zoiper）、外線トランク（フレッツ HGW）、ルーティング |
| 通話記録 | CDR（通話ログ）、電話帳（contacts） |
| オンデマンド発信 | MCP `dial` / `converse` ツール経由 |
| 音声 AI | VAD + STT + LLM + TTS パイプライン、バージイン、プロバイダカタログ |
| プロバイダ | LLM: OpenAI 互換 / Anthropic / Gemini / Vertex AI；TTS: VOICEVOX / Open JTalk；STT: Whisper / Google STT |
| MCP | Streamable HTTP + OAuth 2.1、15 ツール（converse を含む）、claude.ai カスタムコネクタ |
| ワークフロー | IVR + AI ノード（19 種）、xyflow エディタ、AI 自動生成 |
| ネットワーク | dnsmasq DHCP/DNS、nftables NAT、ゼロタッチプロビジョニング（Panasonic / Yealink） |
| Tailscale | tailnet 内線（Zoiper on tailnet から着発信） |
| SSO | SAML 2.0 SP（Microsoft Entra ID / Google Workspace）、SCIM 2.0 自動プロビジョニング |
| 認証・セキュリティ | TOTP 2FA、セッション epoch 即時失効、CSRF、レート制限/ロックアウト、SIP ACL、発信権限、監査ログ、秘密の暗号化保存、socket-proxy |

## ドキュメント一覧

| ページ | 概要 |
|---|---|
| [クイックスタート](quickstart.md) | ワンライナーインストールと初回ログイン |
| [ネットワーク設定](network.md) | LAN/DHCP/NAT と netd の概要 |
| [HGW/フレッツ設定](hgw-flets.md) | NTT HGW への内線登録と SDP 設定の注意点 |
| [SSO 設定](sso.md) | SAML SP と SCIM プロビジョニング |
| [プロバイダ追加](providers.md) | LLM / TTS / STT プロバイダの登録方法 |
| [ワークフロー作成](workflows.md) | IVR・AI フローの構築 |
| [MCP 利用](mcp.md) | claude.ai カスタムコネクタと 15 ツール |
| [Tailscale](tailscale.md) | tailnet 接続と内線利用 |
| [Cloudflare Tunnel 公開](cloudflare.md) | 管理 UI の外部公開 |
| [トラブルシュート](troubleshooting.md) | よくある問題と対処 |
| [セキュリティモデル](security-model.md) | 脅威モデルと防御構成 |

### 実機検証 RUNBOOK（詳細手順）

| RUNBOOK | 内容 |
|---|---|
| [RUNBOOK-phase3-ai.md](RUNBOOK-phase3-ai.md) | 音声 AI パイプライン実機検証 |
| [RUNBOOK-phase4a-mcp.md](RUNBOOK-phase4a-mcp.md) | MCP エージェント実機検証 |
| [RUNBOOK-phase4b-workflow.md](RUNBOOK-phase4b-workflow.md) | ワークフロー実機検証 |
| [RUNBOOK-phase5-netd.md](RUNBOOK-phase5-netd.md) | netd + ネットワーク実機検証 |
| [RUNBOOK-phase6-auth.md](RUNBOOK-phase6-auth.md) | 認証強化 + 公開耐性チェックリスト |
| [ops/deployment.md](ops/deployment.md) | デプロイ / 更新 / ロールバック / バックアップ |
