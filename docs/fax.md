# FAX 受信・送信

millicall は FreeSWITCH の mod_spandsp（`rxfax`）で FAX を受信し、PDF に変換して保存します。
受信した FAX は管理画面の **FAX**（`/faxes`）の「受信」タブで一覧・ダウンロード・削除できます。
受信完了は [Webhook](webhooks.md) の `fax.received` イベントでも通知できます。

PDF をアップロードして外線トランク経由で FAX を **送信** することもできます（`txfax`。下記「[FAX 送信](#fax-送信)」）。

## 仕組み

```
着信 ─ public（トランク）─ transfer ─▶ default: fax_<番号> extension
                                       answer → rxfax <data>/fax/<uuid>.tif → hangup
FreeSWITCH ─ CUSTOM spandsp::rxfaxresult ─▶ core
                                       TIFF → PDF（img2pdf）・faxes テーブルに記録・Webhook
```

- 初版は **G.711 パススルーのみ**（`fax_enable_t38=false`）。NTT ひかり電話 HGW は G.711 で FAX が通り、T.38 には対応していません。
- 原本 TIFF は PDF と同じ場所（`data/fax/<uuid>.tif`）に残ります。PDF は `data/fax/<uuid>.pdf`。
- PDF 化は [img2pdf](https://gitlab.mister-muffin.de/josch/img2pdf)（pikepdf 依存）で行い、CCITT G3/G4 の画像を
  劣化なしで埋め込みます。Pillow は上限チェック（TIFF のヘッダ読み込み）にだけ使います。
  Pillow の PDF 書き出しは 1bit の FAX 画像（WhiteIsZero・FillOrder=2）で白黒反転・崩れが起きるため使いません（#143）。
- ページサイズは TIFF の解像度タグから縦横別に決めるため、標準解像度（204×98 dpi）でも縦横比が保たれます。
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
上限チェックは画素をデコードせずに行い、通過した TIFF だけを PDF にします。PDF は一度メモリ上で組み立てるため、
メモリ使用量はおおむね TIFF サイズ（上限 50 MB）に比例します。
TIFF が壊れていて PDF にできない場合は PDF なしで記録し、ログにはファイル名だけを出します。

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
4. 外部の FAX 機から 0445895784 へ送信し、**FAX** の「受信」タブに PDF が出ることを確認します。
   宛先欄にはトランクの DID（未設定ならユーザー名）が入ります。

## FAX 送信

管理画面の **FAX**（`/faxes`）→「送信」タブで、宛先番号・トランクを指定して PDF をアップロードすると送信キューに入り、
順番に送信されます。送信ボックスで状態（送信待ち / 再試行待ち / 送信中 / 送信済み / 失敗 / 取消）・ページ数・
試行回数・エラーを確認でき、失敗したジョブの **再送**、送信待ちジョブの **取消** ができます（一覧は自動更新）。
結果は [Webhook](webhooks.md) の `fax.sent`（成功）・`fax.failed`（再試行しても送れなかった最終失敗）でも通知できます。

### 仕組み

```
PDF アップロード ─▶ core: 別プロセスで PDFium（pypdfium2）がページをラスタライズ
                        → 1bit・幅 1728 px・204×196 dpi（ファイン）の複数ページ TIFF（G4）
                        data/fax/out/<id>.pdf / <id>.tif、fax_outbox テーブルに登録
送信ワーカー ─ ESL ─▶ originate {origination_uuid=<uuid>,origination_caller_id_number=<トランクの発信者番号>,
                        absolute_codec_string=PCMU,fax_enable_t38=false,fax_verbose=false,
                        ignore_early_media=true,originate_timeout=60}
                        sofia/gateway/<トランク>/<番号> &txfax(<data>/fax/out/<id>.tif)
FreeSWITCH ─ CHANNEL_HANGUP_COMPLETE ─▶ core
                        variable_fax_success / fax_result_text / fax_remote_station_id と hangup_cause を
                        origination_uuid で対応付けて反映・Webhook
```

- トランクを指定しなければ **既定の発信トランク**（有効なトランクの名前順先頭。内線からの外線発信と同じ）を使います。
  発信者番号はトランクの「表示番号」（未設定なら DID）です。
- 宛先は **0 から始まる 10〜11 桁の国内番号** に限ります（国際番号・184/186 前置は送信できません）。
- PDF → TIFF の変換は Ghostscript を使わず、core イメージ同梱の `pypdfium2`（PDFium）で行います。
  信頼できない PDF を扱うため **別プロセス** で実行し、120 秒のタイムアウト（超過で kill）と
  メモリ上限（1 GB、Linux の RLIMIT_DATA）を掛けています。横長のページは 90 度回転して送ります。
- アップロードは PDF のマジックバイト（`%PDF-`）を確認し、保存ファイル名はサーバー採番（`<id>.pdf`）のみです。
- 送信 API は **管理者** のみ利用できます。`MILLICALL_FAX_SEND_ALLOW_USERS=true` にすると一般ユーザーも送信でき、
  その場合一般ユーザーが参照・操作できるのは自分が登録したジョブだけです。
- 送信・再送・取消は監査ログに残ります（宛先番号・トランク・ページ数のみ。ファイル内容は記録しません）。

| API | 内容 |
|---|---|
| `POST /api/faxes/outbox` | multipart（`file`=PDF, `to_number`, `trunk_name` 任意）→ 202 + ジョブ |
| `GET /api/faxes/outbox` | 一覧（新しい順、`limit` / `offset`） |
| `GET /api/faxes/outbox/{id}` | 詳細 |
| `POST /api/faxes/outbox/{id}/retry` | 失敗・取消したジョブを再送（試行回数をリセット） |
| `POST /api/faxes/outbox/{id}/cancel` | 送信待ち（再試行待ちを含む）のジョブを取消 |
| `GET /api/faxes/outbox/{id}/pdf` | 元 PDF。`fax.sent` / `fax.failed` の `pdf_url` の署名付きトークン（24 時間有効）でも取得可 |

### 制約・目安

| 項目 | 内容 |
|---|---|
| 方式 | **G.711（PCMU）パススルーのみ**。NTT ひかり電話 HGW は T.38 非対応のため `fax_enable_t38=false` |
| アップロード上限 | 20 MB（超過は 413）・100 ページ（超過は 422） |
| 同時送信数 | 既定 1（`MILLICALL_FAX_SEND_CONCURRENCY`）。ひかり電話の同時通話数（通常 2ch）を FAX が占有するため、通話と FAX を合わせて上限を超えないよう設定してください |
| 所要時間の目安 | 相手が 9600〜14400 bps で受ける場合、A4 文書 1 ページあたり 30 秒〜1 分程度（写真・細かい網掛けは長くなります）。応答待ちは最大 60 秒 |
| 1 回の送信の上限 | `MILLICALL_FAX_MAX_DURATION_SEC`（既定 1800 秒）を超えると切断し、失敗として扱います |
| 保持 | `MILLICALL_FAX_RETENTION_DAYS` が 1 以上なら、終了済み（送信済み・失敗・取消）の送信ジョブも PDF/TIFF ごと削除します |

### 失敗時の再試行

- 話中（`USER_BUSY`）・無応答（`NO_ANSWER` 等）・回線混雑・**送信中のエラー**（応答後に `txfax` が失敗した場合）は、
  **1 分後・5 分後** に自動で再試行し、**最大 3 回** で打ち切って失敗（`fax.failed`）にします。
- 「この番号は使われていません」（`UNALLOCATED_NUMBER`）・着信拒否など、再試行しても結果が変わらない理由は 1 回で失敗にします。
- FreeSWITCH から結果イベントが届かない場合（発信自体ができなかった等）は、送信ワーカーが `uuid_exists` で通話の有無を確認し、
  通話が無ければその試行を失敗として再試行します。
- core を再起動したとき送信中だったジョブは、FreeSWITCH 側で通話が続いていればそのまま結果を待ち、
  通話が無ければ **結果不明の失敗** にします（重複送信を避けるため自動では再送しません。必要なら再送してください）。

## 運用（compose の更新が必要）

FreeSWITCH と core が同じ絶対パスで TIFF を共有するため、freeswitch サービスに次の bind mount が必要です
（`docker-compose.yml` / `deploy/docker-compose.prod.yml` に追加済み）。

```yaml
      - ./data/fax:/app/data/fax
```

既存環境は `millicallctl update` で compose ファイルを更新してください（compose を更新せずにイメージだけ
更新すると、FAX は受信できても core から TIFF が見えず PDF 化されません）。`data/fax` は core 起動時に作成されます。

FAX 送信でも同じマウントを使います。送信用の TIFF は core が `data/fax/out/<id>.tif`（ディレクトリ 0755・ファイル 0644）に書き、
FreeSWITCH の `txfax` が同じ絶対パスで読みます。

## トラブルシュート

| 症状 | 確認 |
|---|---|
| 一覧に出ない | FreeSWITCH ログに `FAX RECEIVE <番号>` があるか。ESL が `CUSTOM spandsp::rxfaxresult` を購読しているか（core 再起動で再購読） |
| 「失敗」になる | 結果欄の理由（spandsp の `fax_result_text`）。回線品質・相手機の ECM/V.17 設定 |
| PDF が無い（成功なのに） | freeswitch に `./data/fax:/app/data/fax` がマウントされているか。core ログに「空き容量が不足」が出ていないか |
| FAX 受信番号が効かない | core ログに「FAX 保存先パスに使用できない文字」が出ていないか（`data_dir` に空白・記号を含むと FAX extension を出力しません） |
| 自動検知しない | トランクの切替先が有効な FAX 受信番号か。相手機が CNG を出しているか（自動送信のみ） |
| 送信が「送信待ち」のまま | core ログに「FAX 送信ワーカー」のエラーが無いか。同時送信数（`MILLICALL_FAX_SEND_CONCURRENCY`）分の送信中ジョブが残っていないか |
| 送信が `488` / `INCOMPATIBLE_DESTINATION` で失敗 | トランクが PCMU を受け付けるか（HGW は PCMU のみ） |
| 送信が「送信エラー: ...」で失敗 | 結果欄の理由（spandsp の `fax_result_text`）。相手機の ECM 設定・回線品質。FreeSWITCH ログの `txfax` |
| 送信で TIFF が見つからない | freeswitch に `./data/fax:/app/data/fax` がマウントされているか |
