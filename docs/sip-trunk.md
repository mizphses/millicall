# インターネット SIP トランク（Brastel my050 / 一般 SIP 050）

millicall の外線トランクは 2 種別あります。用途に応じて `/trunks` の「種別」で選びます。

| 種別 | 用途 | 想定接続先 |
|---|---|---|
| **HGW** | NTT フレッツ光の HGW（ホームゲートウェイ）に LAN 内で接続 | `192.168.1.1` 等の LAN IP |
| **インターネット SIP** | インターネット越しの SIP プロバイダに登録 | Brastel my050 `softphone.spc.brastel.ne.jp` 等 |

HGW 種別は HGW の癖に合わせた設定（DTMF 無効化・Session-Timers・RFC1918 限定 ACL）を使うため、
そのままではインターネット SIP プロバイダに使えません。**種別を「インターネット SIP」にすると**、
テンプレートが以下を自動で切り替えます。

- **DTMF**: RFC2833 を有効化（一般 SIP / IVR で必須）
- **NAT 越え**: `ext-sip-ip` / `ext-rtp-ip` に外部アドレスを設定（NAT 内・フレッツ直付けの両対応）
- **着信 ACL**: RFC1918 限定ではなく、**トランクごとに許可した CIDR（プロバイダ IP 帯）**のみ許可

## 登録手順（Brastel my050 の例）

1. 管理 GUI `/trunks` で「トランクを追加」
2. 各項目を入力:

   | 項目 | 値（例） |
   |---|---|
   | 名前（識別子） | `brastel_my050` |
   | 種別 | **インターネット SIP** |
   | 表示名 | `Brastel my050` |
   | ホスト名 | `softphone.spc.brastel.ne.jp` |
   | ユーザー名 | Brastel の SIP 認証 ID（例: `44425296`） |
   | パスワード | Brastel の SIP パスワード |
   | DID 番号 | 着信を受ける 050 番号 |
   | 発信者番号 | 発信時に通知する番号 |
   | 着信先内線番号 | 受電させる内線 / AI / ワークフロー番号 |
   | 着信許可 IP 帯（CIDR） | プロバイダの SIP サーバ IP 帯（1 行 1 件） |

3. 「保存」すると対象トランクのプロファイルが再起動され、直ちに REGISTER が試行されます。
4. 一覧の状態バッジが「登録済み」になれば成功です。API では:

   ```bash
   docker compose exec freeswitch fs_cli -x "sofia status gateway brastel_my050"
   # State に REGED が出れば登録成功
   ```

## 着信許可 IP 帯（CIDR）について

インターネット SIP トランクの着信 SIP ポートは、HGW 種別の共通 ACL（`millicall_trusted`＝RFC1918）
では守れません。**プロバイダの SIP サーバ IP 帯を CIDR で許可**してください（例: `203.0.113.0/24`）。

- **空欄にすると着信 ACL を掛けません**（その SIP ポートが WAN に開放されます）。動作確認には使えますが、
  本番では必ずプロバイダの IP 帯を設定してください。
- Brastel 等の許可すべき IP 帯は各プロバイダの技術資料で確認してください。

## NAT / 外部 IP の設定

`ext-sip-ip` / `ext-rtp-ip` は既定で **`auto-nat`** です（NAT 内でも公開 IP でも機能）。固定したい場合は
`.env` で上書きします。

```bash
# ~/millicall/.env
# STUN で外部 IP を解決する場合
MILLICALL_SIP_EXTERNAL_IP=stun:stun.freeswitch.org
# 固定グローバル IP を直接指定する場合
# MILLICALL_SIP_EXTERNAL_IP=203.0.113.10
```

HGW 傘下（NAT 内）で使う場合は、HGW 側で当該トランクの送信元 SIP ポート（既定 `5080` から +2 ずつ
自動採番／`/trunks` で明示指定可）に対する UDP ポート転送が必要になることがあります。

## トラブルシュート

| 症状 | 確認・対処 |
|---|---|
| `UNREGED` / `FAIL_WAIT` / 未登録 | ユーザー名・パスワード・ホスト名を確認。`sofia status gateway <name>` の失敗理由（401/403 等）を確認 |
| 登録は成功するが着信しない | 着信許可 CIDR にプロバイダの SIP サーバ IP 帯が含まれているか確認。DID 番号のルーティングを確認 |
| 片方向しか音声が聞こえない | NAT 越えの問題。`MILLICALL_SIP_EXTERNAL_IP` と HGW のポート転送を確認 |
| DTMF（プッシュ操作）が効かない | 種別が「インターネット SIP」になっているか確認（HGW 種別は DTMF を無効化している） |

> `realm` はホスト名と同じ値を使います。プロバイダが認証 realm に別ドメインを要求する場合は、
> `sofia status gateway <name>` の認証チャレンジ内容に合わせて調整が必要です。
