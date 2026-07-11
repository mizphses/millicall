#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/mizphses/millicall/main"
MILLICALL_HOME="${MILLICALL_HOME:-$HOME/millicall}"

log()  { printf '\033[1;34m[millicall]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[millicall] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 前提チェック ---
command -v docker >/dev/null 2>&1 || die "docker が見つかりません。先に Docker を導入してください。"
docker compose version >/dev/null 2>&1 || die "docker compose v2 が必要です。"

arch="$(uname -m)"
if [ "$arch" != "x86_64" ] && [ "$arch" != "amd64" ]; then
  log "警告: millicall のイメージは現時点で amd64 専用です (現在: ${arch})。このホストでは動作しない可能性があります。"
fi

log "インストール先: ${MILLICALL_HOME}"
mkdir -p "${MILLICALL_HOME}/data"
# data/ ディレクトリのパーミッションを 700 に制限する（M7: 他ユーザーからの読み取り防止）
chmod 700 "${MILLICALL_HOME}/data"
cd "${MILLICALL_HOME}"

# --- compose と .env.example を取得 (compose は常に最新へ) ---
log "compose / .env.example を取得中..."
curl -fsSL "${REPO_RAW}/deploy/docker-compose.prod.yml" -o docker-compose.yml
curl -fsSL "${REPO_RAW}/.env.example" -o .env.example

# --- 初回のみ .env を対話生成 ---
if [ ! -f .env ]; then
  cp .env.example .env
  default_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  : "${default_ip:=192.168.1.10}"

  read -r -p "サーバの LAN IP [${default_ip}]: " lan_ip </dev/tty || true
  lan_ip="${lan_ip:-$default_ip}"
  read -r -p "リリース版 (latest 推奨 / dev / vX.Y.Z) [latest]: " ver </dev/tty || true
  ver="${ver:-latest}"
  read -r -p "HTTPS を使う(本番) cookie_secure [true/false] [false]: " cs </dev/tty || true
  cs="${cs:-false}"

  sed -i "s#^MILLICALL_SIP_DOMAIN=.*#MILLICALL_SIP_DOMAIN=${lan_ip}#" .env
  # sofia の sip-ip=auto は host ネットワーク Docker 環境でループバック(127.0.0.1)へ
  # 誤解決することがあり、その場合 REGISTER の Contact が不正になって HGW が 503 を
  # 返し外線トランクが登録できない。LAN IP を明示バインドしてこれを防ぐ。
  # .env.example ではコメントアウトされているので、コメント有無どちらでも上書きする。
  if grep -qE '^#? *MILLICALL_SIP_BIND_IP=' .env; then
    sed -i -E "s|^#? *MILLICALL_SIP_BIND_IP=.*|MILLICALL_SIP_BIND_IP=${lan_ip}|" .env
  else
    printf 'MILLICALL_SIP_BIND_IP=%s\n' "${lan_ip}" >> .env
  fi
  sed -i "s#^MILLICALL_COOKIE_SECURE=.*#MILLICALL_COOKIE_SECURE=${cs}#" .env
  if grep -q '^MILLICALL_VERSION=' .env; then
    sed -i "s#^MILLICALL_VERSION=.*#MILLICALL_VERSION=${ver}#" .env
  else
    printf 'MILLICALL_VERSION=%s\n' "${ver}" >> .env
  fi
  # .env にはシークレット・認証情報が含まれるため 600 に制限する（M7）
  chmod 600 .env
  log ".env を生成しました。"
else
  log ".env は既存のものを使用します (上書きしません)。"
fi

# --- millicallctl を配置 ---
if [ -w /usr/local/bin ]; then
  ctl_dir="/usr/local/bin"
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  ctl_dir="/usr/local/bin"
  use_sudo=1
else
  ctl_dir="${HOME}/.local/bin"
  mkdir -p "${ctl_dir}"
fi
log "millicallctl を ${ctl_dir} に配置します。"
tmp_ctl="$(mktemp)"
trap 'rm -f "${tmp_ctl}"' EXIT
curl -fsSL "${REPO_RAW}/millicallctl" -o "${tmp_ctl}"
if [ "${use_sudo:-0}" = "1" ]; then
  sudo install -m 0755 "${tmp_ctl}" "${ctl_dir}/millicallctl"
else
  install -m 0755 "${tmp_ctl}" "${ctl_dir}/millicallctl"
fi
rm -f "${tmp_ctl}"

# --- pull & up ---
log "イメージを取得して起動します..."
docker compose pull
docker compose up -d

log "完了しました。"
log "初期管理者パスワードは初回起動ログに一度だけ表示されます:"
log "  millicallctl logs core | grep 初期管理者"
case ":${PATH}:" in
  *":${ctl_dir}:"*) : ;;
  *) log "注意: ${ctl_dir} が PATH にありません。次を実行してください: export PATH=\"${ctl_dir}:\$PATH\"" ;;
esac
