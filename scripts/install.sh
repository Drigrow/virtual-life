#!/usr/bin/env bash
#
# virtual-life 安装入口：交互配置 .env（API key / 登录凭据）→ 部署
# （环境检测/venv/依赖/systemd 服务）→ 可选本地备份。
#
# 用法:
#   bash scripts/install.sh              # 交互式安装（推荐）
#   bash scripts/install.sh --no-restart # 非交互参数会透传给 deploy.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

EXTRA=""
if [ -t 0 ]; then
  read -r -p "配置本地备份（运行数据 cp 到隔壁 ${APP_DIR}-data/）? [Y/n]: " ans || true
  case "$ans" in
    n|N|no|NO) EXTRA="" ;;
    *) EXTRA="--with-backup" ;;
  esac
fi
[ $# -gt 0 ] && EXTRA="$*"

exec bash "$SCRIPT_DIR/deploy.sh" $EXTRA
