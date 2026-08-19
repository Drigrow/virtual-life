#!/usr/bin/env bash
#
# virtual-life 卸载：停止并删除本应用的 systemd 服务、移除备份 cron/配置、
# 删除 venv、可选删除 .env / 运行数据 / 备份数据目录 / 下载的 uv。
# 用法: bash scripts/uninstall.sh          # 交互确认（默认全保留，逐项询问）
#       bash scripts/uninstall.sh --yes    # 全部删除（跳过确认）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="${BACKUP_DATA_DIR:-${APP_DIR}-data}"
if [ -n "${SYSTEMD_UNIT_DIR:-}" ]; then
  UNIT_DIR="$SYSTEMD_UNIT_DIR"
else
  UNIT_DIR=$(printf '%s\n' ${SYSTEMD_UNIT_DIRS:-/etc/systemd/system} | awk 'NF{print $1}')
fi

ASK=1
[ "${1:-}" = "--yes" ] && ASK=0

say() { printf '%s\n' "$*"; }
die() { printf '!! %s\n' "$*" >&2; exit 1; }
confirm() { # prompt -> 0/1（--yes 时直接通过）
  if [ "$ASK" = 0 ]; then return 0; fi
  local ans
  read -r -p "$1 [y/N]: " ans || return 1
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

if [ "$(id -u)" != 0 ] && [ "$ASK" = 1 ]; then
  die "请用 root 运行（需要 systemctl 与删除系统目录）"
fi

say "== virtual-life 卸载 =="
say "应用目录: $APP_DIR"
say "单元目录: $UNIT_DIR"
say "数据目录: $DATA_DIR"
say ""

# 1) systemd 服务（凡是 WorkingDirectory=本目录 的单元，含 virtual-life-2 等）
units=$(grep -l "WorkingDirectory=$APP_DIR" "$UNIT_DIR"/*.service 2>/dev/null || true)
if [ -n "$units" ]; then
  for u in $units; do
    name=$(basename "$u" .service)
    if confirm "停止并删除服务 $name？"; then
      systemctl stop "$name" 2>/dev/null || true
      systemctl disable "$name" 2>/dev/null || true
      rm -f "$u"
      say "已删除 $u"
    fi
  done
  systemctl daemon-reload 2>/dev/null || true
else
  say "未找到本应用的 systemd 单元（$UNIT_DIR/*.service 中无 WorkingDirectory=$APP_DIR）"
fi

# 2) 备份 cron + 配置
if confirm "移除备份 cron 与配置（backup.conf）？"; then
  crontab -l 2>/dev/null | grep -v 'virtual-life-backup' | crontab - 2>/dev/null || true
  rm -f "$DATA_DIR/backup.conf"
  say "已移除备份 cron 与配置"
fi

# 3) venv（含用户手动建的 myenv 等）
if confirm "删除应用目录下的虚拟环境（.venv / venv / myenv 等）？"; then
  rm -rf "$APP_DIR/.venv" "$APP_DIR/venv" "$APP_DIR/env" "$APP_DIR/myenv"
  say "已删除虚拟环境"
fi

# 4) 部署脚本下载的 uv
if confirm "删除下载的 uv 二进制（/usr/local/bin/uv 与 $APP_DIR/.uv）？"; then
  rm -f /usr/local/bin/uv
  rm -rf "$APP_DIR/.uv"
  say "已删除 uv"
fi

# 5) .env（含 API key / 密码）
if confirm "删除 .env（API key / 密码）？"; then
  rm -f "$APP_DIR/.env"
  say "已删除 .env"
fi

# 6) 运行数据
if confirm "删除运行数据（history.md / memory.md / chat_images 等）？"; then
  rm -f "$APP_DIR"/*.md "$APP_DIR"/*.json
  rm -rf "$APP_DIR/chat_images"
  say "已删除运行数据"
fi

# 7) 备份数据目录
if confirm "删除备份数据目录 $DATA_DIR？"; then
  rm -rf "$DATA_DIR"
  say "已删除 $DATA_DIR"
fi

say ""
say "完成。若还要连同代码一起删除：rm -rf $APP_DIR"
