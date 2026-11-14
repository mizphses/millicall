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

> **実機注意**: DTMF がインバンドでしか届かないため、ワークフローの `menu` / `dtmf_input` ノードを使う場合は FreeSWITCH が DTMF イベントをインバンド音声から検出できているか確認が必要です（RUNBOOK-phase4b-workflow.md § 5（リポジトリ runbooks/ 参照） 参照）。

### Session-Expires（セッションタイマー）

HGW は `Session-Expires: 300`（5 分）を要求します。長時間通話やワークフロー実行中に park が続く場合、セッションが切れる可能性があります。FreeSWITCH の session-timer 設定で対処します。

### コーデック（G.711 μ-law / PCMU のみ）

HGW が受け付ける音声コーデックは **G.711 μ-law（PCMU）のみ**です。PCMA（A-law）でオファーすると `488 Not Acceptable Here` で発信が失敗します。

- HGW 種別のトランクへの外線発信は、bridge / originate 文字列に `{absolute_codec_string=PCMU}` を付けて B レグを PCMU 固定でオファーします（core が自動生成）。
- 内線プロファイルは `inbound-codec-negotiation=greedy` で FreeSWITCH 側の優先順（PCMU 先頭）でコーデックを決めるため、通常はトランスコードなしで中継されます。
- 内線端末が PCMA でしか接続できない場合でも、FreeSWITCH が PCMA ⇔ PCMU を変換するので発信できます（変換分の CPU 負荷のみ）。

## 発信者番号通知（186 プレフィックス）

この HGW 回線は **デフォルト非通知**です。相手に発信者番号を通知したい場合は、発信番号の先頭に `186` を付けます。

| 通知方法 | 発信番号の指定例 |
|---|---|
| 非通知（デフォルト） | `09000000000` |
| 番号通知 | `18609000000000` |

MCP ツールの `dial` / `converse` で発信する際も同様です。`caller_id` 引数を指定することで `origination_caller_id_number` が設定されます（未指定時はトランクの `caller_id` が使用されます）。

> `MILLICALL_SIP_REJECT_ANONYMOUS` は **デフォルト `false`** です。HGW 回線では非通知着信が通常の着信に含まれるため、これを `true` にすると実着信がすべて拒否されます。絶対に `true` にしないでください（RUNBOOK-phase6-auth.md § 7（リポジトリ runbooks/ 参照） 参照）。

## 発信回線の選び方（発信プレフィクス）

フレッツ光の追加番号（Flets01 / Flets02 など）やインターネット SIP（Brastel 050 など）を複数登録しているとき、
**どのトランクから発信するか**（= 相手に表示される番号）をダイヤルで選べます。

1. `/trunks` で各トランクの「発信プレフィクス」を設定します（例: Flets02 に `92`、Brastel に `93`）。
2. 内線から **プレフィクス + 外線番号** をダイヤルすると、プレフィクスを外した番号をそのトランクから発信し、
   番号通知にはそのトランクの「発信者番号」を使います。
3. プレフィクスなしで外線番号をダイヤルすると、設定画面「電話運用」の **既定の発信トランク** から発信します
   （未設定なら「発信に使う」トランクの名前順先頭 = 従来の動作）。

| やりたいこと | ダイヤル例 | 実際の発信 |
|---|---|---|
| 既定のトランクから発信 | `0312345678` | 既定トランク → `0312345678` |
| Flets02（プレフィクス `92`）から発信 | `92 07089372750` | Flets02 → `07089372750` |
| Flets02 から番号通知で発信 | `92 186 07089372750` | Flets02 → `18607089372750` |
| Flets02 から非通知で発信 | `92 184 07089372750` | Flets02 → `18407089372750` |

- `184` / `186` はプレフィクスの**後ろ**に付けます（事業者へそのまま渡します）。プレフィクスなしの `186 0…` も外線として発信できます。
- 発信権限（`domestic` / `international`）・国際発信の allowlist・国際番号ブロックは、プレフィクスを外した番号に同じ規則で適用されます。
- プレフィクスに使える文字は `0-9` `*` `#` の 1〜8 文字です。次のものは保存できません: 他トランクと重複・先頭が重なるもの（`92` と `920` など）、
  `0` 始まり、`1` 始まりの 3 桁（`184` / `186` / `110` などの特番と衝突）、`184` / `186` 始まり、内線・AI・ワークフロー・グループ・FAX の番号と同じもの。
- 「発信に使う」をオフにしたトランクは着信専用になり、既定の発信トランクにもプレフィクス発信にも使われません。
- ワークフローの転送ノード・MCP の `dial` / `transfer`・連絡先からの発信でも、宛先にプレフィクスを付ければ同じ規則で回線を選びます。

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
| 外線発信が `488 Not Acceptable Here` | HGW は PCMU のみ。`fs_cli` の `sofia global siptrace on` で B レグの SDP に `PCMU`（payload 0）だけが載っているか確認 |
| 通話が突然切れる | Session-Expires タイムアウトを疑う。HGW の `Session-Expires` 値に合わせた session-timer 設定を確認 |

詳細: RUNBOOK-phase3-ai.md（リポジトリ runbooks/ 参照） / RUNBOOK-phase4b-workflow.md（リポジトリ runbooks/ 参照）
