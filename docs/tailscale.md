# Tailscale 設定

Tailscale を使うと、tailnet（Tailscale のプライベート VPN）上のデバイスから millicall の内線を利用できます。管理 GUI `/network` で設定します。

詳細な実機確認手順は [RUNBOOK-phase5-netd.md](RUNBOOK-phase5-netd.md) を参照してください。

## 仕組み

- **netd** が tailscaled を内部で管理します。`/dev/net/tun` が利用可能な場合はカーネル TUN モード、ない場合は userspace-networking で動作します
- FreeSWITCH が `tailscale0` インターフェースにも待受けするため、tailnet 上の SIP クライアント（Zoiper 等）から普通の内線として発着信できます
- Tailscale の auth key は DB 内で SecretBox 暗号化保存されます（再表示されません）

```
[ tailnet 上の PC/スマホ (Zoiper) ]
        │  SIP over tailscale0
    [ millicall (FreeSWITCH) ]
        │  内線
    [ 他の内線電話機 / HGW 外線 ]
```

## /network ページでの設定手順

1. 管理 GUI `/network` を開く
2. 「Tailscale」セクションで「有効」トグルをオンにする
3. **auth key**（`tskey-auth-...`）を入力する
   - auth key は入力後に保存されますが**再表示されません**（書込専用）
   - auth key の取得: [Tailscale Admin](https://login.tailscale.com/admin/settings/keys) → Keys → Generate auth key
4. 「接続」ボタンを押す
5. 状態がページ上に表示されます（15 秒ポーリング）

> Tailscale の状態は `GET /api/network/tailscale/status` でも確認できます。

## Zoiper で内線登録する手順（tailnet 経由）

1. Tailscale が接続状態（`Status: Running`）であることを確認
2. `tailscale ip` コマンドまたは [Tailscale Admin](https://login.tailscale.com/admin/machines) でサーバの tailnet IP（例: `100.x.x.x`）を確認
3. Zoiper（または他の SIP クライアント）でアカウント設定:

| 項目 | 値 |
|---|---|
| Username | 内線番号（例: `1001`）|
| Password | 内線の SIP パスワード（`/extensions` で確認） |
| Domain / Host | millicall サーバの **tailnet IP**（例: `100.x.x.x`） |
| Transport | UDP |

4. 「Registered（緑）」になれば登録成功
5. tailnet 上から他の内線番号に発信したり、外線（HGW 経由）に着信を転送できます

## Tailscale の切断

1. 管理 GUI `/network` → Tailscale セクションで「切断」ボタンをクリック
2. netd が `tailscale down` を実行します

## トラブルシュート

| 症状 | 確認・対処 |
|---|---|
| 接続ボタンを押しても状態が変わらない | `millicallctl logs netd` で tailscale の起動ログを確認 |
| auth key エラー | Tailscale Admin で有効な auth key か確認。使い捨て（one-off）か再利用可能か確認 |
| tailnet 上から SIP 登録できない | `tailscale ip` でサーバの tailnet IP を確認。Zoiper の Domain に tailnet IP を設定しているか確認 |
| 音声が通じない（片方向） | FreeSWITCH が `tailscale0` に bind しているか確認。`MILLICALL_SIP_BIND_IP` の設定を確認 |
| `/dev/net/tun` がない | userspace-networking で動作。`millicallctl logs netd` で確認 |

詳細: [RUNBOOK-phase5-netd.md § 4](RUNBOOK-phase5-netd.md)
