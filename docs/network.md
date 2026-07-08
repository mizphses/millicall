# ネットワーク設定

millicall の netd コンテナが LAN ルーター機能（DHCP / DNS / NAT / Tailscale）を提供します。管理 GUI の `/network` ページから設定します。

詳細な実機確認手順は RUNBOOK-phase5-netd.md（リポジトリ runbooks/ 参照） を参照してください。

## netd の役割とアーキテクチャ

```
[ WAN (フレッツ HGW) ]
        │
  WAN インターフェース（例: eth0）
        │  nftables masquerade
  LAN インターフェース（例: enp3s0）
        │
[ LAN 電話機・PC ]
  ↑ DHCP option 66 → プロビジョニング URL 配布
```

- **netd** は `host` ネットワーク + `NET_ADMIN` / `NET_RAW` 権限で動作する特権コンテナ。
- core とは UNIX ソケット `/run/millicall/netd.sock` で通信（named volume `millicall-run` を共有）。
- netd が停止していても core は起動し続けます。ネットワーク操作のみエラーになります。
- dnsmasq・nftables・tailscale は netd コンテナ内で直接管理されます。

## /network ページの設定手順

### 1. LAN/DHCP 設定

管理 GUI `/network` を開き「LAN/DHCP」セクションに入力して保存します。

| 項目 | 例 | 説明 |
|---|---|---|
| LAN インターフェース | `enp3s0` | 電話機を接続する NIC |
| LAN IP | `172.20.0.1` | このホストの LAN 側 IP |
| プレフィックス長 | `16` | サブネット（`/16` = 255.255.0.0） |
| DHCP レンジ（開始） | `172.20.1.1` | 電話機への払い出し開始 |
| DHCP レンジ（終了） | `172.20.1.254` | 電話機への払い出し終了 |
| リース時間 | `12h` | DHCP リース有効期間 |
| プロビジョニング URL | （空白推奨） | 空白の場合 `http://<lan_ip>/provisioning/` が自動生成（ポート 80 の場合は省略） |

### 2. NAT 設定

「NAT」セクションで「NAT 有効」をオンにし、WAN インターフェース（HGW 配下の NIC）を選択して保存します。

### 3. 「設定を適用」

**設定を保存しただけでは netd に反映されません。** 「設定を適用」ボタンを押すことで core が netd に `apply_dhcp` → `apply_nat` を送信し、nftables・dnsmasq が実際に書き換えられます。

適用に失敗した場合は 502 エラーとメッセージが表示されます。`millicallctl logs netd` で netd のログを確認してください。

### 4. Tailscale

[Tailscale](tailscale.md) ページを参照してください。

## ゼロタッチプロビジョニング（LAN ポートに挿すだけで内線化）

1. 電話機を LAN ポートに接続
2. dnsmasq が IP + DHCP option 66（プロビジョニング URL）を配布
3. 電話機が `http://<lan_ip>/provisioning/<機種>/...` を自動取得（ポート 80 の場合は省略）
4. **未登録 MAC の場合は 404** が返ります。`/devices` ページに「未割当」として表示されます
5. `/devices` で「リース同期」→「内線割当」（内線番号 + 表示名）→「クイックプロビジョン」
6. 電話機が設定を再取得して SIP REGISTER → 内線化完了

> **セキュリティ**: プロビジョニング URL の応答は **LAN CIDR 内の送信元のみ** 許可されます（WAN からは 404）。また端末ごとのワンタイムトークンゲートがあり、未認可の端末には認証情報を返しません。

対応機種テンプレート: **Panasonic**（ConfigCommon.cfg / Config{MAC}.cfg）、**Yealink**（boot / common.cfg / {mac}.cfg）、電話帳 XML。

## API 操作（参考）

```bash
# 現在のネットワーク設定を取得（auth key はマスク）
curl -b cookie.txt http://192.168.1.10/api/network

# 設定を更新
curl -X PUT -b cookie.txt http://192.168.1.10/api/network \
  -H 'Content-Type: application/json' \
  -d '{"lan_interface":"enp3s0","lan_ip":"172.20.0.1","prefix_len":16,...}'

# netd へ適用
curl -X POST -b cookie.txt http://192.168.1.10/api/network/apply
```

## トラブルシュート

| 症状 | 確認・対処 |
|---|---|
| 「設定を適用」が 502 | `millicallctl logs netd` で netd 起動を確認。UNIX ソケット共有が正しいか確認 |
| 電話機が設定を取得できない | 送信元 IP が LAN CIDR 内か確認。デバイスが `provisioned` + 内線割当済みか確認 |
| リース同期が 502 | netd 未起動、または電話機未接続で dnsmasq リースファイルが未生成 |
| NAT が効かない | WAN インターフェース名が正しいか確認。「設定を適用」を実行済みか確認 |

詳細: RUNBOOK-phase5-netd.md § 5. トラブルシュート（リポジトリ runbooks/ 参照）
