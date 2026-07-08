# HGW / フレッツ光電話 設定

millicall を NTT フレッツ光電話の HGW（ホームゲートウェイ）に接続するための設定と注意点です。

## HGW の内線登録モデル

NTT の HGW は、配下の SIP 端末を「内線」として登録する構成を採ります。millicall は HGW に対して **SIP 端末（内線）として登録**し、外線着信を受け取ります。

```
[ 公衆電話網 (NTT 光電話) ]
        │
    [ HGW ]  ← millicall が内線として登録
        │  SIP (UDP 5060)  ← 内線番号（例: 30）を使って着信が届く
    [ millicall (FreeSWITCH) ]
        │  SIP
    [ 内線電話機 / Zoiper ]
```

## HGW への登録手順

管理 GUI `/trunks` でトランクを作成します。主な設定項目:

| 項目 | 値の例 | 説明 |
|---|---|---|
| 名前 | `hikari` | トランクの識別名 |
| 表示名 | `光電話` | UI 表示用 |
| SIP サーバ | `192.168.1.1` | HGW の LAN 側 IP |
| ユーザー名 | `HGW に登録した内線番号（例: 30）` | SIP username |
| パスワード | `HGW に設定した SIP パスワード` | 自動生成される場合あり |
| 発信者番号 | `0312345678` | 契約者番号（非通知時は空欄） |

> SIP パスワードは DB 内で暗号化（Fernet）保存されます。

## HGW の RFC 3264 厳格 SDP 制約

NTT ひかり電話の HGW は SDP の Answer を **RFC 3264 に厳格**に処理します。以下の設定を守らないと着信が失敗します。

### DTMF 設定

HGW は **RFC 2833（電話イベント / payload type 101）の renegotiation を受け付けません**。そのため FreeSWITCH の設定は次のようにします。

```xml
<!-- data/freeswitch/sip_profiles/external.xml（core が自動生成） -->
<param name="dtmf-type" value="none"/>
<!-- rfc2833 を申告しない。DTMF はインバンド（in-band audio）で受信 -->
```

この設定は core が自動生成する FreeSWITCH 設定ファイルに反映されています。`rfc2833-pt=0` として宣言することで HGW との SDP ネゴシエーションが成立します。

> **実機注意**: DTMF がインバンドでしか届かないため、ワークフローの `menu` / `dtmf_input` ノードを使う場合は FreeSWITCH が DTMF イベントをインバンド音声から検出できているか確認が必要です（[RUNBOOK-phase4b-workflow.md § 5](RUNBOOK-phase4b-workflow.md) 参照）。

### Session-Expires（セッションタイマー）

HGW は `Session-Expires: 300`（5 分）を要求します。長時間通話やワークフロー実行中に park が続く場合、セッションが切れる可能性があります。FreeSWITCH の session-timer 設定で対処します。

## 発信者番号通知（186 プレフィックス）

この HGW 回線は **デフォルト非通知**です。相手に発信者番号を通知したい場合は、発信番号の先頭に `186` を付けます。

| 通知方法 | 発信番号の指定例 |
|---|---|
| 非通知（デフォルト） | `09000000000` |
| 番号通知 | `18609000000000` |

MCP ツールの `dial` / `converse` で発信する際も同様です。`caller_id` 引数を指定することで `origination_caller_id_number` が設定されます（未指定時はトランクの `caller_id` が使用されます）。

> `MILLICALL_SIP_REJECT_ANONYMOUS` は **デフォルト `false`** です。HGW 回線では非通知着信が通常の着信に含まれるため、これを `true` にすると実着信がすべて拒否されます。絶対に `true` にしないでください（[RUNBOOK-phase6-auth.md § 7](RUNBOOK-phase6-auth.md) 参照）。

## FreeSWITCH 設定の確認

core が正常に起動すると、HGW 接続用の設定ファイルが自動生成されます。

```bash
# external profile の状態確認（トランク登録状況）
docker compose exec freeswitch fs_cli -x "sofia status"

# トランクの登録状態確認
docker compose exec freeswitch fs_cli -x "sofia status gateway hikari"
# Status が "REGED" なら登録成功
```

## 着信番号について

HGW からの着信で `destination_number` に届く番号は、**公衆電話番号ではなく HGW が割り当てた内線番号**（例: `30`）の場合があります。実際に届く番号は FreeSWITCH のログで確認してください。

```bash
millicallctl logs freeswitch | grep "INBOUND"
# または
docker compose exec freeswitch fs_cli -x "sofia loglevel all 5"
```

確認した番号を管理 GUI `/routes` でルーティング設定に使用します。

## トラブルシュート

| 症状 | 確認・対処 |
|---|---|
| トランクが `NOREG` / 未登録 | HGW 側の内線登録設定を確認。SIP パスワードが一致しているか確認 |
| 着信しない | `sofia status gateway hikari` で `REGED` を確認。ルーティング設定（`/routes`）で着信番号が正しいか確認 |
| DTMF が効かない | インバンド DTMF 設定を確認。`docker compose logs freeswitch` で DTMF イベントを確認 |
| 通話が突然切れる | Session-Expires タイムアウトを疑う。HGW の `Session-Expires` 値に合わせた session-timer 設定を確認 |

詳細: [RUNBOOK-phase3-ai.md](RUNBOOK-phase3-ai.md) / [RUNBOOK-phase4b-workflow.md](RUNBOOK-phase4b-workflow.md)
