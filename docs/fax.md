# FAX 受信

millicall は FreeSWITCH の mod_spandsp（`rxfax`）で FAX を受信し、PDF に変換して保存します。
受信した FAX は管理画面の **受信 FAX**（`/faxes`）で一覧・ダウンロード・削除できます。
受信完了は [Webhook](webhooks.md) の `fax.received` イベントでも通知できます。

## 仕組み

```
着信 ─ public（トランク）─ transfer ─▶ default: fax_<番号> extension
                                       answer → rxfax <data>/fax/<uuid>.tif → hangup
FreeSWITCH ─ CUSTOM spandsp::rxfaxresult ─▶ core
                                       TIFF → PDF（Pillow）・faxes テーブルに記録・Webhook
```

- 初版は **G.711 パススルーのみ**（`fax_enable_t38=false`）。NTT ひかり電話 HGW は G.711 で FAX が通り、T.38 には対応していません。
- 原本 TIFF は PDF と同じ場所（`data/fax/<uuid>.tif`）に残ります。PDF は `data/fax/<uuid>.pdf`。
- 標準解像度（204×98 dpi）の FAX は縦横比を補正してから PDF にします。
- FAX の API（`/api/faxes`・`/api/fax-receivers`）は他の管理 API と同様に **管理者ログインが必須** です。
  例外として `GET /api/faxes/{id}/pdf` は、`fax.received` Webhook の `pdf_url` に付く署名付きトークン
  （24 時間有効・その FAX 専用）でも取得できます。
- 受信番号の追加・変更・削除と FAX の削除は監査ログに残ります。

## 上限と保持

| 項目 | 既定 | 設定 |
|---|---|---|
| 1 回の受信の上限時間（`sched_hangup ... ALLOTTED_TIMEOUT`） | 1800 秒 | `MILLICALL_FAX_MAX_DURATION_SEC` |
| 保持日数（0 = 削除しない。1 以上なら日次で行と TIFF/PDF を削除） | 0 | `MILLICALL_FAX_RETENTION_DAYS` |
| PDF 変換の上限: TIFF サイズ / ページ数 / 1 ページの画素数 | 50 MB / 100 / 3456×4800×2 | 固定 |
| PDF 変換に必要な空き容量（未満なら変換をスキップし警告ログ） | 200 MB | 固定 |

変換上限を超えた FAX は「失敗（PDF 変換を中止: ...）」として記録し、TIFF は残します。
PDF はページを 1 枚ずつ書き出すため、ページ数が多くてもメモリ使用量は 1 ページ分に収まります。

## 設定手順

### 1. FAX 受信番号を作る

**ルーティング**（`/routes`）の「FAX 受信」セクションで **FAX 受信番号を追加** します。
番号は内線・AI・ワークフロー・グループと同じ統一番号プラン（2〜6 桁）を共有します（例: `300`）。

### 2a. FAX 専用回線: トランクの着信先にする

**外線トランク** の「着信先内線番号」で、手順 1 の番号（種別「FAX 受信」）を選びます。
その回線への着信はすべて FAX として受信します。

### 2b. 音声・FAX 併用回線: FAX 自動検知

トランク編集の **FAX 自動検知（音声・FAX 併用回線）** で次を設定します。

| 項目 | 内容 |
|---|---|
| FAX の呼出音（CNG）を検知したら FAX 受信に切り替える | 有効化 |
| 切替先の FAX 受信番号 | 手順 1 の番号 |
| 検知時間（秒） | 5〜120（既定 20） |
| 検知中に案内音声を再生 | 任意（下記） |

有効にすると public コンテキストの着信 extension で、通常の transfer の前に

```xml
<action application="spandsp_start_fax_detect" data="transfer '300 XML default' 20 cng"/>
```

を実行します。CNG（発呼側 FAX の 1100Hz 断続音）を検知すると FAX 受信番号へ切り替わり、
検知しなければ通常どおり着信先（内線・ワークフロー等）で応対します。
media bug はチャネルに付くため、検知窓の間はワークフロー実行中や内線との通話中でも有効です。

注意:

- `spandsp_start_fax_detect` は内部で **pre_answer（183 + SDP の早期メディア）** を行います（mod_spandsp の仕様）。
  発信者側では呼出音が早期メディアに切り替わります。
- **案内音声あり** の場合は先に応答（answer）し、検知を仕掛けてから案内（`tts_cache_dir/builtin/fax_receive_notice.wav`、#123 で同梱）を再生し、通常の着信先へ進みます。案内中に CNG を検知した場合も FAX へ切り替わります。
- 手動送信など **CNG を出さない FAX 機は検知できません**。確実に受けたい相手には FAX 専用番号（2a）を案内してください。
- 着信先がすでに FAX 受信番号のトランクでは検知は出力されません（受信中の CNG で受信をやり直さないため）。
- 切替先に使われている FAX 受信番号は削除できません。番号を変更するとトランクの切替先も追従します。

## Flets02（0445895784 / HGW 内線 4）での設定例

1. HGW の内線設定で内線 4 を millicall 用に登録済みであること（[HGW/フレッツ設定](hgw-flets.md)）。
2. ルーティングで FAX 受信番号 `300`（名前「代表 FAX」）を追加。
3. 外線トランク `Flets02`（ユーザー名 `4`、DID `0445895784`）を編集し、
   - FAX 専用で試す場合: 着信先内線番号 = `300`
   - 電話と併用する場合: 着信先は従来の内線/ワークフローのまま、FAX 自動検知を有効化し切替先 `300`
4. 外部の FAX 機から 0445895784 へ送信し、**受信 FAX** に PDF が出ることを確認します。
   宛先欄にはトランクの DID（未設定ならユーザー名）が入ります。

## 運用（compose の更新が必要）

FreeSWITCH と core が同じ絶対パスで TIFF を共有するため、freeswitch サービスに次の bind mount が必要です
（`docker-compose.yml` / `deploy/docker-compose.prod.yml` に追加済み）。

```yaml
      - ./data/fax:/app/data/fax
```

既存環境は `millicallctl update` で compose ファイルを更新してください（compose を更新せずにイメージだけ
更新すると、FAX は受信できても core から TIFF が見えず PDF 化されません）。`data/fax` は core 起動時に作成されます。

## トラブルシュート

| 症状 | 確認 |
|---|---|
| 一覧に出ない | FreeSWITCH ログに `FAX RECEIVE <番号>` があるか。ESL が `CUSTOM spandsp::rxfaxresult` を購読しているか（core 再起動で再購読） |
| 「失敗」になる | 結果欄の理由（spandsp の `fax_result_text`）。回線品質・相手機の ECM/V.17 設定 |
| PDF が無い（成功なのに） | freeswitch に `./data/fax:/app/data/fax` がマウントされているか。core ログに「空き容量が不足」が出ていないか |
| FAX 受信番号が効かない | core ログに「FAX 保存先パスに使用できない文字」が出ていないか（`data_dir` に空白・記号を含むと FAX extension を出力しません） |
| 自動検知しない | トランクの切替先が有効な FAX 受信番号か。相手機が CNG を出しているか（自動送信のみ） |
