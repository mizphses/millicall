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
- **NAT 越え**: `ext-sip-ip` / `ext-rtp-ip` に外部アドレスを設定（NAT 内で使う場合は[後述の設定](#nat-越え)が必要）
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
   | OPTIONS キープアライブ間隔（秒、0 で無効） | **`0`**（Brastel は OPTIONS に応答しないため。後述） |
   | 登録更新間隔（秒） | NAT 内（HGW 傘下）なら **`60`**〜`120`（NAT の穴を維持するため。後述）。公開 IP 直付けなら既定 `3600` |
   | 発信プレフィクス | 例: `93`（「93 + 外線番号」でこのトランクから発信。[後述](#発信回線の選び方発信プレフィクス)） |
   | 発信に使う | 着信専用にするならオフ |
   | 番号通知 | 掛けたとおり（既定）/ 常に通知（186）/ 常に非通知（184）。事業者が 184/186 に対応していない場合は「掛けたとおり」のまま |

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

## NAT 越え

HGW 傘下（NAT 内）のサーバからインターネット SIP を使うと、「登録済み」なのに外から掛けると
無音のまま着信しない（`sofia status profile external_<名前>` で `CALLS-IN 0`、INVITE が 1 件も
届いていない）ことがあります。次の 3 点を確認してください。

### 1. 外部アドレス（`MILLICALL_SIP_EXTERNAL_IP`）を指定する

`ext-sip-ip` / `ext-rtp-ip` は既定で **`auto-nat`** です。`auto-nat` は公開 IP の直付けや
UPnP / NAT-PMP に対応したルータ配下では機能しますが、**HGW が UPnP / NAT-PMP に対応していないと
外部 IP を得られず**（FreeSWITCH の起動ログに `Auto-NAT false` が出て Ext-SIP-IP が空になる）、
REGISTER の Contact や SDP が私設アドレス（`192.168.x.x` 等）のままになります。この場合は
`.env` で STUN か公開 IP を指定してください。

```bash
# ~/millicall/.env
# STUN で外部 IP を解決する場合
MILLICALL_SIP_EXTERNAL_IP=stun:stun.freeswitch.org
# 固定グローバル IP を直接指定する場合
# MILLICALL_SIP_EXTERNAL_IP=203.0.113.10
```

反映後に `sofia status profile external_<名前>` の `Ext-SIP-IP` が公開 IP になっていることを確認します。

### 2. 登録更新間隔を 60〜120 秒にする

HGW の UDP NAT エントリは通信が無いと短時間で閉じます。REGISTER の更新間隔が既定の 3600 秒のままで、
ほかに NAT の穴を維持する通信が無いと、エントリが閉じた後は事業者からの INVITE がサーバに届きません。
トランク編集画面の **「登録更新間隔（秒）」を `60`〜`120`** にして保存してください
（gateway の `expire-seconds`。60〜3600 秒で指定でき、API では `register_expire_seconds`）。
定期的な再 REGISTER が NAT の穴を開け続けます。

### 3. OPTIONS に応答しない事業者は OPTIONS 間隔を `0` にする

OPTIONS キープアライブも NAT の穴を維持しますが、Brastel 050 など OPTIONS に応答しない事業者では
登録がフラップします（[後述](#options-キープアライブpingについて)）。その場合は OPTIONS 間隔を `0` にし、
NAT の維持は上記 2. の登録更新間隔で行います。

上記で改善しない場合は、HGW 側で当該トランクの送信元 SIP ポート（既定 `5080` から +2 ずつ
自動採番／`/trunks` で明示指定可）に対する UDP ポート転送を設定してください。

## 発信回線の選び方（発信プレフィクス）

HGW のトランクと併用するとき、外線発信は既定では設定画面「電話運用」の **既定の発信トランク**
（未設定なら「発信に使う」トランクの名前順先頭）から出ます。050 番号から掛けたいときは、
このトランクに「発信プレフィクス」（例: `93`）を設定し、**プレフィクス + 外線番号** をダイヤルします。

| やりたいこと | ダイヤル例 | 実際の発信 |
|---|---|---|
| 050 番号から発信 | `93 07089372750` | このトランク → `07089372750`（番号通知はトランクの発信者番号） |
| 050 番号から番号通知で発信 | `93 186 07089372750` | このトランク → `18607089372750` |
| 既定のトランクから発信 | `07089372750` | 既定の発信トランク |

050 番号を常に使うなら、プレフィクスを付けずに「既定の発信トランク」をこのトランクにしても構いません。
発信権限・国際発信の allowlist はプレフィクスを外した番号に同じ規則で適用されます。
プレフィクスの制約（使える文字・重複不可など）は [HGW / フレッツ光電話 設定](hgw-flets.md#発信回線の選び方発信プレフィクス) を参照してください。

## OPTIONS キープアライブ（ping）について

各トランクの gateway は既定で 30 秒ごとに SIP OPTIONS を送り、相手の生存を確認します
（FreeSWITCH の gateway param `ping`）。NTT HGW は OPTIONS に `405` を返し、これは応答あり
（成功）として扱われるため既定の `30` のままで問題ありません。

一方、Brastel 050 など **OPTIONS に応答しない事業者** では毎回 `408` タイムアウトとなり、
FreeSWITCH が gateway を DOWN 扱いにして unregister → 再 REGISTER を繰り返します。
その結果、REGISTER 自体は成功しているのに約 60 秒周期で「登録済み」と「未登録」を往復します。
FreeSWITCH のログには次のような行が繰り返し出ます。

```
[WARNING] sofia.c Unregister brastel01
[WARNING] sofia.c Ping failed brastel01 with code 408 - count 1/0/1, state DOWN
[WARNING] sofia_reg.c brastel01 Failed Registration [0], setting retry to 30 seconds.
```

この場合はトランク編集画面の「OPTIONS キープアライブ間隔（秒、0 で無効）」を **`0`** にして
保存してください。`0` のときは `ping` param 自体を出力しません（OPTIONS を送らない）。
値は 0〜3600 秒で指定でき、API では `options_ping_seconds` です。

## トラブルシュート

| 症状 | 確認・対処 |
|---|---|
| 登録が約 60 秒ごとに「登録済み」⇔「未登録」を往復する | ログに `Ping failed ... 408` が出ていれば OPTIONS 非応答の事業者。OPTIONS キープアライブ間隔を `0` にする（上記参照） |
| `UNREGED` / `FAIL_WAIT` / 未登録 | ユーザー名・パスワード・ホスト名を確認。`sofia status gateway <name>` の失敗理由（401/403 等）を確認 |
| 登録は成功するが着信しない | 着信許可 CIDR にプロバイダの SIP サーバ IP 帯が含まれているか確認。DID 番号のルーティングを確認 |
| 登録済みだが外から掛けると無音（`CALLS-IN 0`） | NAT の穴が閉じて INVITE が届いていない。登録更新間隔を `60`〜`120` にし、`MILLICALL_SIP_EXTERNAL_IP` に STUN か公開 IP を指定する（[NAT 越え](#nat-越え)） |
| 片方向しか音声が聞こえない | NAT 越えの問題。`MILLICALL_SIP_EXTERNAL_IP` と HGW のポート転送を確認 |
| DTMF（プッシュ操作）が効かない | 種別が「インターネット SIP」になっているか確認（HGW 種別は DTMF を無効化している） |

> `realm` はホスト名と同じ値を使います。プロバイダが認証 realm に別ドメインを要求する場合は、
> `sofia status gateway <name>` の認証チャレンジ内容に合わせて調整が必要です。
