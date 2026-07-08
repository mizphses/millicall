# トラブルシュート

よくある問題と確認コマンド・対処方法をまとめます。

## 全般的な確認コマンド

```bash
# コンテナの状態確認
millicallctl ps

# 各サービスのログ確認
millicallctl logs core         # core（FastAPI）のログ
millicallctl logs freeswitch   # FreeSWITCH のログ
millicallctl logs netd         # netd のログ

# ヘルスチェック
curl http://127.0.0.1/healthz
# → {"status":"ok"} が返れば正常
```

---

## 着信しない / HGW から届かない

### 症状
外線（HGW 経由）に電話してもつながらない。

### 確認手順

**1. トランク登録状態を確認**

```bash
docker compose exec freeswitch fs_cli -x "sofia status gateway hikari"
# Status が "REGED" であることを確認
```

`NOREG` の場合は HGW の内線登録設定とパスワードを確認してください。

**2. ルーティング設定を確認**

管理 GUI `/routes` で着信番号に対するルートが設定されているか確認します。

**3. HGW から届く着信番号を確認**

着信番号は HGW の内線番号（例: `30`）で届く場合があります。

```bash
millicallctl logs freeswitch | grep "INBOUND"
```

**4. SIP ACL の確認**

`MILLICALL_SIP_TRUSTED_CIDRS`（デフォルト: RFC1918 全帯域 + loopback）に HGW の IP が含まれているか確認します。HGW が `192.168.1.1` であればデフォルトの `192.168.0.0/16` に内包されているので通常は問題ありません。

**5. HGW の SDP 制約**

SDP ネゴシエーションの失敗の場合は [HGW/フレッツ設定](hgw-flets.md) を確認してください（`dtmf-type=none` / `rfc2833-pt=0`）。

詳細: RUNBOOK-phase3-ai.md § 8（リポジトリ runbooks/ 参照） / RUNBOOK-phase6-auth.md § 7（リポジトリ runbooks/ 参照）

---

## AI 応答が遅い（TTS 遅延）

### 症状
着信して AI が応答するまでに 2 秒以上かかる。

### 確認手順

**1. 遅延ログを確認**

```bash
millicallctl logs core | grep "AI latency"
# 例: AI latency: utterance_end -> first playback = 1850 ms (uuid=...)
```

**2. TTS プロバイダを確認**

外部 TTS API（クラウド系）を使っている場合は遅延が大きくなります。**ローカル TTS**（VOICEVOX または Open JTalk）への切り替えを検討してください。目標遅延: ローカル TTS 使用時 1000ms 以下。

**3. LLM の応答文長を確認**

LLM が最初に長い文を生成していると TTS への入力が遅れます。システムプロンプトで「応答は2文以内で」などと制約してください。

**4. プロバイダ接続テスト**

```bash
curl -b cookie.txt -X POST http://127.0.0.1/api/providers/<id>/test
```

詳細: RUNBOOK-phase3-ai.md § 8（リポジトリ runbooks/ 参照）

---

## ログイン失敗でロックアウト（429 Too Many Requests）

### 症状
ログイン画面で「429 Too Many Requests」または「Retry-After: N」が返る。

### 仕様

- **IP しきい値**: 10 回（`MILLICALL_LOGIN_MAX_ATTEMPTS`）/ 300 秒（`MILLICALL_LOGIN_LOCKOUT_SECONDS`）
- **ユーザー名しきい値**: 30 回（`MILLICALL_LOGIN_USERNAME_MAX_ATTEMPTS`）
- ロックアウト期間が経過すると自動解除されます

### 対処

ロックアウト期間（デフォルト 300 秒 = 5 分）経過後に再試行してください。  
ロックアウト期間を短くしたい場合は `.env` で `MILLICALL_LOGIN_LOCKOUT_SECONDS` を調整して `millicallctl update` します。

監査ログ（管理 GUI `/audit`）でロックアウトイベントを確認できます。

詳細: RUNBOOK-phase6-auth.md § 4（リポジトリ runbooks/ 参照）

---

## CSRF 403 エラー（管理 GUI 操作時）

### 症状
管理 GUI で操作（保存・削除等）すると `403 Forbidden` が返る。

### 原因と対処

CSRF double-submit cookie の検証失敗です。以下を確認してください。

1. **Cookie が有効か**: ブラウザの開発者ツールで `millicall_csrf` Cookie が存在するか確認
2. **SameSite 設定**: `MILLICALL_COOKIE_SECURE=true` にしているのに HTTP アクセスしていないか確認
3. **ブラウザのキャッシュ**: ページをリロードしてから再試行
4. **API 直接アクセス**: API クライアントから操作する場合は `X-CSRF-Token` ヘッダに Cookie 値を設定する必要があります

詳細: RUNBOOK-phase6-auth.md § 5（リポジトリ runbooks/ 参照）

---

## netd 未起動で 502（/network 操作時）

### 症状
管理 GUI `/network` で「設定を適用」すると 502 エラーが返る。

### 確認手順

```bash
# netd の状態確認
millicallctl ps | grep netd

# netd のログ確認
millicallctl logs netd

# UNIX ソケットの存在確認
ls -la ~/millicall/data/
# または named volume 内を確認（コンテナから）
docker compose exec core ls -la /run/millicall/
```

### 対処

- netd が起動していない場合: `millicallctl up` で再起動
- ソケットが存在しない: `millicall-run` named volume が正しくマウントされているか `docker compose config` で確認

詳細: RUNBOOK-phase5-netd.md § 5（リポジトリ runbooks/ 参照）

---

## プロビジョニング設定が取得できない（電話機が設定を取得できない）

### 症状
電話機を LAN に接続したが、SIP 登録されない。

### 確認手順

**1. デバイスが /devices ページに表示されているか確認**

```bash
# dnsmasq がリースを配布しているか確認
millicallctl logs netd | grep "dnsmasq"
```

**2. 送信元 IP が LAN CIDR 内か確認**

プロビジョニング URL の応答は LAN CIDR 内からのリクエストのみ許可されます。

**3. デバイスが「内線割当済み」か確認**

管理 GUI `/devices` でデバイスが `provisioned` 状態で内線番号が割り当てられているか確認します。未割当の MAC には 404 が返ります。

**4. HTTP resync の確認**

```bash
millicallctl logs core | grep "provisioning"
```

詳細: RUNBOOK-phase5-netd.md § 3、§ 5（リポジトリ runbooks/ 参照）

---

## MCP /mcp が 401（トークン失効）

### 症状
claude.ai から MCP ツールを呼ぶと認証エラーになる。

### 原因と対処

OAuth トークンはインメモリ保持のため、`docker compose restart core` 等でプロセスが再起動するとすべてのトークンが失効します。

**対処**: claude.ai のコネクタ設定から「再認証」を実行してください。

詳細: RUNBOOK-phase4a-mcp.md § 7（リポジトリ runbooks/ 参照） / [MCP 利用](mcp.md)

---

## freeswitch が起動しない

### 症状
`millicallctl ps` で freeswitch が起動していない。

### 確認手順

```bash
# core が healthy になっているか確認（freeswitch は core healthy 待ち）
millicallctl ps | grep core

# core のログを確認
millicallctl logs core | tail -50

# freeswitch 設定ファイルが生成されているか確認
ls ~/millicall/data/freeswitch/
```

FreeSWITCH は core が `service_healthy` になってから起動します。core のログでマイグレーションや設定生成のエラーを確認してください。

---

## arm64 ホストで動作しない

現時点で全イメージ（core / freeswitch / netd）が **amd64 専用**です。arm64 ホストへのデプロイは将来課題です。

詳細: [ops/deployment.md](ops/deployment.md)
