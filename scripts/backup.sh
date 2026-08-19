#!/usr/bin/env bash
#
# virtual-life 备份：配置 + 执行（部署后跑一次 setup 即可）
#
# 备份内容：*.md / *.json / chat_images（不含 .env 等密钥）
# 默认模式 local：cp 到隔壁 <应用目录>-data/ 文件夹；
# 可选模式 both/remote：同时/仅推送到远程 git 仓库（每周）。
# 可重复运行 setup 修改选择；cron 由 setup 自动安装/替换。
#
# 用法：
#   bash scripts/backup.sh setup                              # 交互式配置（默认 local）
#   bash scripts/backup.sh setup --mode both --remote <URL>   # 非交互：本地+远程
#   bash scripts/backup.sh run                                # 执行备份（cron 自动调用）
#   bash scripts/backup.sh status                             # 查看当前配置
#   bash scripts/backup.sh off                                # 移除 cron 与配置
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="${BACKUP_DATA_DIR:-${APP_DIR}-data}"
CONF="$DATA_DIR/backup.conf"
CRON_TAG="virtual-life-backup"
CRON_LOG="/var/log/virtual-life-backup.log"

say() { printf '%s\n' "$*"; }
die() { printf '!! %s\n' "$*" >&2; exit 1; }

ask() {
  local prompt="$1" default="$2" ans
  read -r -p "$prompt [$default]: " ans
  printf '%s\n' "${ans:-$default}"
}

load_conf() {
  MODE=local
  REMOTE_URL=""
  BRANCH=main
  [ -f "$CONF" ] && . "$CONF"
  MODE="${MODE:-local}"
  BRANCH="${BRANCH:-main}"
}

prepare_remote() {
  local remote="$1"
  git -C "$DATA_DIR" init -q 2>/dev/null || true
  # 统一本地分支为 $BRANCH（git init 默认分支可能是 master）
  git -C "$DATA_DIR" checkout -q -B "$BRANCH" 2>/dev/null || true
  if git -C "$DATA_DIR" remote get-url origin >/dev/null 2>&1; then
    git -C "$DATA_DIR" remote set-url origin "$remote"
  else
    git -C "$DATA_DIR" remote add origin "$remote"
  fi
  git -C "$DATA_DIR" config user.name  >/dev/null 2>&1 || git -C "$DATA_DIR" config user.name  "AutoBackup"
  git -C "$DATA_DIR" config user.email >/dev/null 2>&1 || git -C "$DATA_DIR" config user.email "backup@localhost"
  cat > "$DATA_DIR/.gitignore" << 'EOF'
.env
.venv/
__pycache__/
*.py[cod]
*.pyo
.DS_Store
*.bak
EOF
  git -C "$DATA_DIR" add -A
  if ! git -C "$DATA_DIR" diff --cached --quiet; then
    git -C "$DATA_DIR" commit -m "initial backup $(date -u +'%Y-%m-%dT%H:%M:%SZ')" || true
  fi
  say "== 尝试推送初始快照 =="
  if ! GIT_TERMINAL_PROMPT=0 git -C "$DATA_DIR" push -u origin "$BRANCH" 2>/tmp/vl-backup-push.err; then
    say "!! 初始推送失败（认证/权限问题），本地备份不受影响："
    cat /tmp/vl-backup-push.err
  fi
}

install_cron() {
  local mode="$1" schedule
  if [ "$mode" = "local" ]; then
    schedule="0 * * * *"        # 每小时 cp 本地
  else
    schedule="0 3 * * 0"        # 每周日 03:00 cp + 推送远程
  fi
  crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab - 2>/dev/null || true
  (crontab -l 2>/dev/null; echo "$schedule /usr/bin/flock -n /tmp/$CRON_TAG.lock bash $SCRIPT_DIR/backup.sh run >> $CRON_LOG 2>&1 # $CRON_TAG") | crontab -
  say "已安装 cron: $schedule (tag $CRON_TAG)"
}

setup() {
  local arg_mode="" arg_remote=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --mode)   arg_mode="$2";   shift 2 ;;
      --remote) arg_remote="$2"; shift 2 ;;
      *) shift ;;
    esac
  done

  load_conf
  say "== 当前配置 =="
  say "  应用目录: $APP_DIR"
  say "  数据目录: $DATA_DIR"
  say "  模式: $MODE"
  [ -n "$REMOTE_URL" ] && say "  远程: $REMOTE_URL (分支 $BRANCH)"
  say ""

  local mode="${arg_mode:-$(ask '备份模式 [local=仅本地cp / both=本地+远程 / remote=仅远程]' "$MODE")}"
  case "$mode" in
    local|both|remote) ;;
    *) die "无效模式: $mode" ;;
  esac

  local remote="$arg_remote"
  if [ -z "$remote" ] && { [ "$mode" = "both" ] || [ "$mode" = "remote" ]; }; then
    remote="$(ask '远程仓库 URL（空则回退 local）' "${REMOTE_URL:-}")"
  fi
  if [ -z "$remote" ] && { [ "$mode" = "both" ] || [ "$mode" = "remote" ]; }; then
    say "未提供远程 URL，回退为 local"
    mode=local
  fi

  mkdir -p "$DATA_DIR"
  { echo "MODE=$mode"; echo "REMOTE_URL=${remote:-}"; echo "BRANCH=${BRANCH:-main}"; } > "$CONF"

  if [ "$mode" = "both" ] || [ "$mode" = "remote" ]; then
    prepare_remote "$remote"
  fi
  install_cron "$mode"

  say ""
  say "== 完成 =="
  say "  模式: $mode"
  [ -n "$remote" ] && say "  远程: $remote (分支 $BRANCH)"
  say "  手动备份: bash $SCRIPT_DIR/backup.sh run"
}

run() {
  load_conf
  [ -f "$CONF" ] || die "未配置，请先运行: bash $SCRIPT_DIR/backup.sh setup"
  mkdir -p "$DATA_DIR"

  cp -f "$APP_DIR"/*.md   "$DATA_DIR/" 2>/dev/null || true
  cp -f "$APP_DIR"/*.json "$DATA_DIR/" 2>/dev/null || true
  if [ -d "$APP_DIR/chat_images" ]; then
    mkdir -p "$DATA_DIR/chat_images"
    cp -f "$APP_DIR"/chat_images/* "$DATA_DIR/chat_images/" 2>/dev/null || true
  fi

  if [ "$MODE" = "both" ] || [ "$MODE" = "remote" ]; then
    [ -n "$REMOTE_URL" ] || die "配置为远程模式但缺少 REMOTE_URL"
    git -C "$DATA_DIR" add -A
    if git -C "$DATA_DIR" diff --cached --quiet; then
      say "remote: 无变更，跳过提交"
    else
      git -C "$DATA_DIR" commit -m "auto backup $(date -u +'%Y-%m-%dT%H:%M:%SZ')" || true
    fi
    if ! GIT_TERMINAL_PROMPT=0 git -C "$DATA_DIR" push origin "$BRANCH" 2>/tmp/vl-backup-push.err; then
      say "!! 远程推送失败："
      cat /tmp/vl-backup-push.err
    fi
  fi
  say "备份完成 $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
}

status() {
  load_conf
  say "应用目录: $APP_DIR"
  say "数据目录: $DATA_DIR ($(du -sh "$DATA_DIR" 2>/dev/null | cut -f1 || echo '?') )"
  say "模式: $MODE"
  [ -n "$REMOTE_URL" ] && say "远程: $REMOTE_URL (分支 $BRANCH)"
  say "cron:"
  crontab -l 2>/dev/null | grep "$CRON_TAG" || say "  (无)"
}

off() {
  crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab - 2>/dev/null || true
  rm -f "$CONF"
  say "已移除 cron 与配置。数据目录 $DATA_DIR 保留（如需删除请手动 rm -rf）。"
}

case "${1:-}" in
  setup)  shift; setup "$@" ;;
  run)    run ;;
  status) status ;;
  off)    off ;;
  *) die "用法: $0 {setup|run|status|off}" ;;
esac
