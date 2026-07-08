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
  subprocess は `shell=False` の argv。プロビジョニング配布は LAN 限定 + 端末ワンタイム
  トークン + 既知デバイス限定の三重ゲート。tailscale auth key は SecretBox 暗号化。

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
   ワンタイムトークン発行 + FreeSWITCH 再生成 + 端末へ best-effort HTTP resync）。
4. 電話機が設定を再取得して SIP REGISTER → 内線化完了。

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
   SIP 登録成功（LAN 限定 + トークンゲートを通過すること）。
5. **HTTP resync**: Panasonic `/admin/resync`・`/cgi-bin/api-provision`、Yealink
   `servlet?key=AutoProvision`。Basic 認証は `MILLICALL_PHONE_ADMIN_*`（既定=工場出荷値）に依存。
6. **SO_PEERCRED**: Linux コンテナ内で core（同 UID/ root）のみが netd ソケットに接続でき、
   他ユーザは拒否されること。
7. **tailnet 内線**: tailnet 上の Zoiper が普通の内線として発着信できること（FreeSWITCH が
   `tailscale0` にも待受け）。

## 5. トラブルシュート

- **/network の適用が 502**: `docker compose logs netd`。netd が起動しているか、
  ソケット `/run/millicall/netd.sock` が両コンテナで共有されているか。
- **電話機が設定を取得できない**: 送信元 IP が LAN CIDR 内か（LAN 外は 404）、デバイスが
  provisioned + 内線割当済みか、トークンゲートを通過しているか。`docker compose logs core`。
- **リース同期が 502**: netd 未起動、または dnsmasq リースファイル未生成（電話機未接続）。
- **NAT が効かない**: WAN インターフェース名が正しいか、`apply` を実行したか、
  `net.ipv4.ip_forward` が有効か（netd が apply_nat 時に設定）。

## 6. 後片付け

```bash
# NAT 無効化: /network で NAT トグル OFF → 適用（nftables テーブル削除）
# Tailscale 切断: /network の「切断」→ netd tailscale down
# デバイス削除: /devices の削除（DELETE /api/devices/{id}）
```
