# Phase 0: HGWスパイク 検証RUNBOOK

FreeSWITCH sofia が ひかり電話HGW へ REGISTER でき、外線の発着信が双方向音声つきで
通るかを実機で確認する。結果で **go（FreeSWITCH採用）/ no-go（Asterisk案へ差し戻し）** を判断する。

## 0. 前提

- 実機: Ubuntu 24.04 LTS、Docker Engine 24+ / docker compose v2。
- サーバは ひかり電話HGW と同一LAN（同一セグメント）に有線接続されていること。
- HGWの管理画面で「内線設定」を1つ作成し、内線番号・パスワードを控えていること。
- HGWのLAN側IP（例 192.168.1.1）を控えていること。
- 契約電話番号（発信者番号）を控えていること。

## 1. 起動

```bash
cd spike
cp .env.example .env && chmod 600 .env
# .env を実値で編集: HGW_IP / HGW_SIP_USER / HGW_SIP_PASSWORD / OUTBOUND_CALLERID
docker compose up -d
```

docker-compose.yml はイメージを digest で固定済みのため、初回 pull でも
完全に同一のレイヤーが取得される。使用 digest は以下のとおり:

```
safarov/freeswitch@sha256:b31c743f4c911a19687c61e3214968f2a24f93f9d3d667cc26284192e158ffc6
```

(FreeSWITCH 1.10.12 / 2024-08-02。safarov リポジトリにはバージョン別タグが
存在しないため latest タグではなく digest を直接指定している。)

起動ログで生成された external.xml（パスワード伏字）と FreeSWITCH の起動を確認:

```bash
docker compose logs -f freeswitch
# "generated external.xml (password masked)" のブロックが出る
# その後 FreeSWITCH のバナー/ "Sofia SIP Stack" 系ログが出れば起動成功。Ctrl-C で抜ける
```

## 2. REGISTER の確認（最重要）

```bash
docker compose exec freeswitch fs_cli -x "sofia status"
```
期待: `external` プロファイルが `RUNNING` で表示される。

```bash
docker compose exec freeswitch fs_cli -x "sofia status gateway hgw"
```
**成功時の期待出力（抜粋）:**
```
=========================================================
Name       hgw
Profile    external
...
State      REGED
Status     UP
PingFreq   30
...
=========================================================
```
`State REGED` かつ `Status UP` なら REGISTER 成功。

登録の生存を5分間観察（フラップしないこと）:
```bash
watch -n 15 'docker compose exec -T freeswitch fs_cli -x "sofia status gateway hgw" | grep -E "State|Status|FailedCalls"'
```
期待: 5分間 `State REGED` を維持し、UNREGED/FAIL_WAIT に落ちない。

### SIPトレースを見る（切り分け用）

```bash
docker compose exec freeswitch fs_cli
# 対話プロンプトで:
sofia loglevel all 9
sofia global siptrace on
# 以降 REGISTER/INVITE の生SIPが流れる。/quit で抜ける
```
または一発:
```bash
docker compose exec freeswitch fs_cli -x "sofia profile external siptrace on"
docker compose logs -f freeswitch   # 生SIPを観察
```

## 3. 着信テスト（外→内）

1. 携帯電話などから、HGWに紐づく契約電話番号へ発信する。
2. FreeSWITCH が自動応答し、確認トーン（約2秒）の後、echo に入る。
3. 発信側（携帯）で **自分の声が遅延して返ってくれば双方向音声OK**。

ログ確認:
```bash
docker compose logs freeswitch | grep "HGW INBOUND"
# "===== HGW INBOUND: from=... to=... =====" が出る
```
アクティブ通話確認:
```bash
docker compose exec freeswitch fs_cli -x "show calls"
```
期待: 通話中は1件表示され、切断後は0件。

**判定:** 呼び出しに応答し、自分の声が echo で返る = 着信＋双方向音声 成功。

## 4. 発信テスト（内→外）

内線端末を登録しないため `originate` で外線を鳴らす。`<携帯番号>` は自分の携帯等（0始まり）:

```bash
docker compose exec freeswitch fs_cli -x \
  "originate {origination_caller_id_number=$(grep OUTBOUND_CALLERID .env | cut -d= -f2),origination_context=default}sofia/gateway/hgw/<携帯番号> &echo"
```

期待動作:
- 指定した携帯が鳴る。
- 応答すると echo につながり、**話した声が遅延して返る**（双方向音声OK）。
- 発信者番号表示が意図どおり（自局番号 or 非通知）であることを確認。

代替（default dialplan 経由での発信確認）:
```bash
docker compose exec freeswitch fs_cli -x "originate user/dummy <携帯番号> XML default" 2>/dev/null || \
docker compose exec freeswitch fs_cli -x \
  "originate loopback/<携帯番号>/default &echo"
```
ログ:
```bash
docker compose logs freeswitch | grep "HGW OUTBOUND"
```

**判定:** 相手が鳴動・応答し双方向音声が通れば 発信成功。

## 5. 失敗パターン別 切り分け

| 症状 | fs_cli/ログの見え方 | 主原因 | 対処 |
|---|---|---|---|
| **401 が繰り返り登録できない** | siptrace に `401 Unauthorized` の後 再REGISTERされず失敗 | ダイジェスト認証NG（realm不一致/ユーザー名誤り） | `HGW_SIP_USER` を内線番号に、`realm=HGW_IP` を確認。1回目の401→2回目で200なら正常（チャレンジ）。 |
| **403 Forbidden** | siptrace に `403` | パスワード誤り、内線がHGW未登録/使用中 | `.env` のパスワード確認。HGW管理画面で内線が有効か、他機器が同内線を専有していないか確認。 |
| **タイムアウト / State=UNREGED / FAIL_WAIT** | siptraceに応答が全く来ない | HGWに届いていない（IP誤り/別セグメント/ファイアウォール/UDP遮断） | `HGW_IP` を確認。`docker compose exec freeswitch ping -c1 <HGW_IP>`。同一セグメントか、ホストのufw/nftablesが5060/UDPを塞いでいないか。 |
| **gateway DOWN だが発着信は通る** | `Status DOWN` なのに通話成功 | HGWが OPTIONS(ping)に無応答 | external.xml の gateway `ping` を `0` にして再判定（DOWN表示は無視してよい）。 |
| **着信/発信で片方向のみ音声（自分の声が返らない/相手が聞こえない）** | 通話は確立、echoが返らない | RTP不達。sip-ip/rtp-ipが誤NIC、またはRTPポートがファイアウォールで遮断 | `sofia status profile external` の `sip-ip`/`rtp-ip` が HGWと同一セグメントのLAN IPか確認。誤りなら external.xml の `$${local_ip_v4}` を実LAN IPに固定。ホストで RTP(既定16384-32768/udp) を許可。`-nonat` 起動とext-sip-ip未設定を確認。 |
| **応答直後(ACK後~90ms)に HGW から BYE、発信側に「通話できませんでした」** | 200 OK 送信→ACK受信→即 BYE。answer SDP の m= 行に `0 101` | **HGW は RFC 3264 を厳格適用**: オファー(PCMUのみ)に無い telephone-event(101) を answer に含めると拒否 | `rfc2833-pt=0` + `dtmf-type=none` で answer から 101 を除去（**実機確認済みの必須設定**。dtmf-type=none 単独では SDP 広告が消えない点に注意） |
| **INVITE が全く来ない（着信時に何も受信しない）** | siptrace 無反応、`HGW INBOUND` 0件 | HGW が着信を内線に振っていない（内線の着信/鳴動設定） | HGW 管理画面の電話設定で対象電話番号の着信内線に当該内線を割当・有効化 |
| **着信するが即切断/無音のまま切れる** | INVITE後すぐ BYE | コーデック不一致 or Session-Timers | `PCMU,PCMA` になっているか。（注: enable-timer は true で実機動作確認済み。HGW は Session-Expires: 300 を要求する） |
| **fs_cli が繋がらない** | `Error connecting` | コンテナ未起動/起動途中 | `docker compose ps`、`docker compose logs freeswitch` で起動確認。`start_period` 経過を待つ。 |

補助コマンド:
```bash
docker compose exec freeswitch fs_cli -x "sofia status profile external"   # sip-ip/rtp-ip/待受確認
docker compose exec freeswitch fs_cli -x "sofia xmlstatus gateway hgw"     # 詳細
docker compose exec freeswitch ping -c1 <HGW_IP>                           # 疎通
docker compose exec freeswitch fs_cli -x "reloadxml"                       # XML再読込
docker compose restart freeswitch                                          # 設定変更後の再起動
```

## 6. go / no-go 判定基準

以下 **すべて満たせば GO（millicall v2 の音声コアを FreeSWITCH に確定）**:

- [ ] `sofia status gateway hgw` が `State REGED` / `Status UP`（ping無応答個体は ping=0 で UP 相当と判断）。
- [ ] 上記 REGISTER が **5分間フラップせず**維持される。
- [ ] **着信**: 外部から契約番号へ発信 → FreeSWITCH が応答し、echo で **双方向音声**が確認できる。
- [ ] **発信**: originate で外部番号が鳴動・応答 → echo で **双方向音声**が確認できる。
- [ ] 発信時の発信者番号表示が意図どおり（自局番号通知 or 184で非通知）に制御できる。

**NO-GO（Asterisk + externalMedia 案へ差し戻し。設計doc 12章）** の条件（いずれか）:

- REGISTER が全く成功しない、または頻繁にフラップし安定しない。
- REGISTER は成功するが、発信・着信のいずれかが確立できない。
- 通話は確立するが、本RUNBOOK 5章の対処を尽くしても **音声片方向が解消しない**。

判定結果（GO/NO-GO と根拠、使用イメージ digest、確認した gateway status とSIPトレースの要点）をこのRUNBOOK末尾または `docs/superpowers/` の記録に残し、指揮者（Fable）レビューへ回すこと。

### 判定結果（2026-07-05 実機検証）

**GO** — 全項目クリア。詳細は `docs/superpowers/reports/2026-07-05-phase0-hgw-gonogo.md`（gitignore領域）。

- REGISTER: 内線番号=認証ユーザID、realm=HGW LAN IP で `REGED/UP`。5分間フラップなし（Wi-Fi 接続でも安定）。
- 着信: 双方向音声OK。ただし answer SDP から telephone-event を除去する設定（上表）が必須だった。
- 発信: `sofia/gateway/hgw/<番号>` で双方向音声OK。**この回線は非通知がデフォルト**で、`186`+番号 プレフィクスで番号通知を確認（`origination_caller_id_number` は表示に影響せず、通知制御は HGW/網側）。
- HGW は INVITE で `Session-Expires: 300` を要求 → `enable-timer=true` で正常応答。
- HGW の OPTIONS ping には `405 Method Not Allowed` が返るが sofia は疎通成功とみなし `UP` 維持（ping=30 のままで問題なし）。

## 7. 後片付け（検証終了後は必ず実施）

本キットは host network で 5060/RTP/8021 をLANに露出する使い捨て検証用。恒久稼働させない:

```bash
docker compose down
```
