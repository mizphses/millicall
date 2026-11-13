# Phase 5: netd + ネットワーク 実機検証 RUNBOOK

本体を LAN のルータ/DHCP サーバにして、指定ポートに電話機を挿すだけで内線化する
（ゼロタッチプロビジョニング）。NAT マスカレードで LAN→インターネット、Tailscale で
tailnet 内線を提供する。netd（特権デーモン）が dnsmasq/nftables/tailscale を管理する。

## 0. 前提・アーキテクチャ

- **対象 OS**: Ubuntu 24.04 / linux-amd64 固定。
- **netd**: `host` network + `NET_ADMIN`/`NET_RAW` の特権コンテナ。UNIX ソケット
  `/run/millicall/netd.sock`（0660、SO_PEERCRED で core UID 以外を拒否）でのみ待受け。
  dnsmasq/tailscaled をコンテナ内で起動し、nftables を直接適用する。
- **core → netd**: 名前付きボリューム `millicall-run` を両コンテナの `/run/millicall` に
  マウントして UNIX ソケットを共有。core の `NetdClient` は呼び出しごとに接続（遅延）。
  netd が停止していても core は起動する。
- **セキュリティ**: netd は core からの全入力をサーバ側で再検証（IF 名/IP/CIDR/authkey）、
  subprocess は `shell=False` の argv。端末固有のプロビジョニング設定（SIP 認証情報を含む）は
  LAN 限定 + 登録済み端末（provisioned/active・内線割当済み・内線 enabled）+ 送信元 IP が
  端末の IP と一致、の条件で配信する（標準モード）。端末ごとに「厳格モード」を選ぶと、
  加えて管理者が明示発行したトークン付き URL（`/provisioning/t/<token>/...`）が必須になる。
  トークンは自動では発行・配送されない（§ 3.1）。tailscale auth key は SecretBox 暗号化。

## 1. デプロイ

```bash
cd ~/millicall-prod
# .env に必要なら電話機管理者資格情報を上書き（既定は工場出荷値）
#   MILLICALL_PHONE_ADMIN_USERNAME=admin
#   MILLICALL_PHONE_ADMIN_PASSWORD=<機種の管理パスワード>
docker compose pull && docker compose up -d   # core / freeswitch / netd
docker compose ps          # netd が Up であること
docker compose logs netd   # "netd サーバ起動: /run/millicall/netd.sock" を確認
```

netd コンテナは `/dev/net/tun` があれば tailscale をカーネル TUN で、無ければ
userspace-networking で起動する。

## 2. ネットワーク設定（GUI: /network）

1. **LAN/DHCP**: LAN インターフェース（例 `enp3s0`）、LAN IP（例 `172.20.0.1`）、
   プレフィックス（`16`）、DHCP レンジ、リース時間、プロビジョニング URL（空なら
   `http://<lan_ip>/provisioning/` を自動生成（ポート 80 は省略））を入力して保存。
2. **NAT**: 「NAT 有効」トグル + WAN インターフェース（HGW 配下の NIC）。保存。
3. **「設定を適用」** を押す → core が netd に `apply_dhcp`→`apply_nat` を送る。
   - 失敗時は 502 とメッセージ（netd 未起動・適用失敗）が表示される。
4. **Tailscale**: 有効トグル + auth key（`tskey-...`）入力（**書込専用・再表示されない**）→
   保存 → 「接続」。状態はページ上に表示（15 秒ポーリング）。

API で行う場合:
```bash
curl -s http://192.168.1.3/api/network -b cookie.txt            # 現在設定（auth key は返らない）
curl -s -X PUT http://192.168.1.3/api/network -b cookie.txt -H 'Content-Type: application/json' -d '{...}'
curl -s -X POST http://192.168.1.3/api/network/apply -b cookie.txt   # netd へ適用
curl -s http://192.168.1.3/api/network/tailscale/status -b cookie.txt
```

## 3. ゼロタッチプロビジョニング

1. 電話機を LAN ポートに接続 → dnsmasq が IP + DHCP option 66（プロビジョニング URL）を配布。
2. 電話機が `http://<lan_ip>/provisioning/<機種>/...` を自動取得（ポート 80 は省略）。
   - **未登録 MAC には認証情報を返さない**（404）。まず GUI の /devices に「未割当」で現れる。
3. GUI /devices → **「リース同期」** で dnsmasq リースを取り込み → 対象デバイスに
   **「内線割当」**（内線番号 + 表示名）→ quick-provision（Extension 作成 + provisioned +
   FreeSWITCH 再生成 + 端末へ best-effort の再取得指示）。トークンは発行しない（標準モード）。
4. 電話機が設定を再取得して SIP REGISTER → 状態列が「完了」になる。

「完了」は **端末固有の設定ファイルを取得済み（取得記録あり）かつ FreeSWITCH で SIP 登録を
確認済み（端末の IP から当該内線が登録されている）** のときだけ表示する。状態の意味:

| 表示 | 意味 |
|---|---|
| 取得待ち | 内線割当後、まだ端末固有 cfg を配信していない |
| トークン未発行 | 厳格モードでトークンを発行していない（端末固有 cfg は 404） |
| トークン待ち | 厳格モードでトークン付き URL からの取得をまだ確認していない |
| 取得済み（未登録） | cfg は配信したが、その端末 IP からの SIP 登録がない |
| 取得済み（登録未確認） | FreeSWITCH から登録状態を取得できなかった（完了とは表示しない） |
| 完了 | cfg 取得済み かつ SIP 登録確認済み |

「再同期」ボタンは電話機への **再取得の指示** のみ（SIP NOTIFY check-sync と、資格情報が
設定済みなら HTTP）。指示が受理されても電話機が実際に取りに来たかは状態列（取得日時）で
確認する。HTTP の成功判定は 2xx のみ（3xx のログイン画面などは失敗）。

### 3.1 厳格モード（トークン必須・手動配送）

LAN 内の他の機器から SIP 認証情報を取られるリスクを下げたい端末だけに使う。電話機へ
トークン付き URL を自動で設定する手段（ベンダー文書化済み API）は見つかっていないため、
**管理者が電話機の Web 画面へ手入力する**。

1. /devices で対象端末の「厳格モード」→ 確認。以後、通常 URL の端末固有 cfg は 404。
2. 「トークン発行」→ ダイアログに機種別の URL が **一度だけ** 表示される（DB はハッシュのみ。
   監査ログ `device.provision_token.issue` にトークン値は残らない）。
   - Yealink: 自動プロビジョニングのサーバー URL に `http://<lan_ip>/provisioning/t/<token>/`
   - Panasonic: Standard File URL に `.../t/<token>/Panasonic/Config{MAC}.cfg`、
     Master File URL に `.../t/<token>/Panasonic/ConfigCommon.cfg`
3. 電話機側の DHCP によるプロビジョニング（option 66）を無効にする（有効だと通常 URL が
   優先され 404 になりうる）。Yealink はトークン付き URL で取得した common.cfg が
   `static.auto_provision.dhcp_option.enable = 0` を配る（実機要確認）。Panasonic は手動。
4. 電話機を再起動または「再同期」→ 状態列が「完了」になることを確認。
5. 紛失・漏洩時は「トークン再発行」（古い URL は即 404）または「トークン破棄」
   （`device.provision_token.revoke`）。「標準に戻す」とトークンは破棄され、電話機側の URL も
   通常 URL に戻す必要がある。どの場合も自動で認証を弱めるフォールバックはしない。

トークンはクエリ（`?token=`）では受け付けない。パス中のトークンは uvicorn のアクセスログで
`/provisioning/t/***/...` に伏字化する（リバースプロキシ等を前段に置く場合はそちらのログに注意）。

対応機種テンプレート: Panasonic（ConfigCommon.cfg + Config{MAC}.cfg）、Yealink（boot +
common.cfg + {mac}.cfg）、電話帳 XML。

## 4. 要実機確認項目（統合テストでは fake・未検証）

netd の実効はコンテナ+ホストでのみ検証可能。**すべて未検証**。

1. **`docker build` 成功**: netd イメージ（Tailscale apt リポジトリ疎通・`uv sync` 依存解決）。
2. **netd の実権限動作**: `host` net + `NET_ADMIN`/`NET_RAW` + `/dev/net/tun` 下で
   nftables masquerade が効き LAN→インターネット疎通、tailscale up で tailnet 接続。
3. **DHCP 配布**: `apply_dhcp` → `reload-dnsmasq.sh`（SIGHUP／再起動）→ 電話機へ IP +
   option 66 配布。
4. **電話機の設定取得 → REGISTER**: option 66 経由で Panasonic/Yealink が config を取得し
   SIP 登録成功、状態列が「完了」になること。
5. **再取得指示（resync）**: § 6 の実機チェックリストを参照。
6. **SO_PEERCRED**: Linux コンテナ内で core（同 UID/ root）のみが netd ソケットに接続でき、
   他ユーザは拒否されること。
7. **tailnet 内線**: tailnet 上の Zoiper が普通の内線として発着信できること（FreeSWITCH が
   `tailscale0` にも待受け）。

## 5. トラブルシュート

- **/network の適用が 502**: `docker compose logs netd`。netd が起動しているか、
  ソケット `/run/millicall/netd.sock` が両コンテナで共有されているか。
- **電話機が設定を取得できない**: 送信元 IP が LAN CIDR 内か（LAN 外は 404）、デバイスが
  provisioned + 内線割当済みか、送信元 IP が端末の IP と一致しているか（DHCP で IP が
  変わったら「リース同期」）、厳格モードならトークン付き URL を入力しているか。
  `docker compose logs core`。
- **リース同期が 502**: netd 未起動、または dnsmasq リースファイル未生成（電話機未接続）。
- **NAT が効かない**: WAN インターフェース名が正しいか、`apply` を実行したか、
  `net.ipv4.ip_forward` が有効か（netd が apply_nat 時に設定）。

## 6. 実機チェックリスト（issue #85。未検証・所有者の実機で確認）

組み合わせ: {管理者資格情報あり, なし} × {Panasonic, Yealink} × {初回, 再同期, 再起動}、
および厳格モード。各項目で **UI の状態表示と実際の設定取得（core のアクセスログ /
取得日時）が一致すること** を確認する。

- [ ] 初回: 内線割当後、電話機が端末固有 cfg を取得し「完了」になる（資格情報あり/なし、両ベンダー）
- [ ] 再同期: 「再同期」で取得日時が更新される。トーストの手段（SIP NOTIFY / HTTP）と実際に効いた手段が一致する
- [ ] 再起動: 電話機の再起動で取得日時が更新され「完了」に戻る
- [ ] FreeSWITCH 停止中は「取得済み（登録未確認）」になり「完了」と表示されない
- [ ] SIP NOTIFY: `sofia profile internal check_sync <user>@<domain>` で
      Panasonic（`CFG_RESYNC_FROM_SIP="check-sync"`）が再取得する
- [ ] SIP NOTIFY: Yealink が check-sync で再取得する（再起動を伴うかも記録する）
- [ ] Yealink Action URI: common.cfg の `features.action_uri.enable = 1` /
      `features.action_uri_limit_ip = <lan_ip>` 配布後、`/servlet?key=AutoP` が 2xx を返し再取得する
      （認証の要否・画面での許可確認が出ないかも記録）
- [ ] Panasonic HTTP: `/admin/resync`・`/cgi-bin/api-provision?event=resync` は **未検証**。
      応答コードと実際に再取得するかを記録し、効かなければ削除を検討する
- [ ] 資格情報が誤っている場合、HTTP は 401 で失敗として表示される
- [ ] 厳格モード（両ベンダー）: トークン付き URL を手入力 → 取得 → 「完了」。boot / common
      経由で取得先 URL がトークン付きのまま維持され、24 時間ごとの定期取得でも 404 にならない
- [ ] 厳格モード: 電話機の DHCP option 66 を有効のままにした場合の挙動（通常 URL が優先されるか）
- [ ] 厳格モード: Yealink の `static.auto_provision.dhcp_option.enable = 0` が効く
- [ ] トークン再発行後、古い URL の電話機は 404（状態は「トークン待ち」）になる
- [ ] core のアクセスログにトークンが平文で出ない（`/provisioning/t/***/`）

## 7. 後片付け

```bash
# NAT 無効化: /network で NAT トグル OFF → 適用（nftables テーブル削除）
# Tailscale 切断: /network の「切断」→ netd tailscale down
# デバイス削除: /devices の削除（DELETE /api/devices/{id}）
```
