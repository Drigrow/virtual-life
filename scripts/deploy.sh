#!/usr/bin/env bash
#
# virtual-life 一键部署：
#   1) 检测 Python 3.10+；没有则尝试 apt 安装（python3 + python3-venv + python3-pip）
#   2) 创建/复用 venv 并 pip install -r requirements.txt
#      - 若已有激活的 venv（$VIRTUAL_ENV）则直接使用它
#      - 默认用 <应用目录>/.venv；以 bin/pip 是否可执行判断 venv 是否完整，
#        半成品（如缺 pip）自动清理重建
#      - 创建失败（Debian 常见缺 ensurepip）→ 自动 apt install ${PY}-venv 后重试
#   3) 生成 systemd 服务（服务名自动防撞：virtual-life / virtual-life-2 / ...），
#      daemon-reload + enable + start
#   4) 可选：自动配置备份（scripts/backup.sh，默认 local cp）
#
# 用法：
#   bash scripts/deploy.sh                # 部署 + 启动
#   bash scripts/deploy.sh --with-backup  # 部署后顺便配置 local 备份
#   bash scripts/deploy.sh --no-restart   # 只装好/更新服务与 enable，不重启（供二次运行）
#
# 测试钩子（一般不用）：
#   SYSTEMD_UNIT_DIRS=...   自定义单元目录（默认 /etc/systemd/system /usr/lib/systemd/system /lib/systemd/system）
#   SKIP_ROOT_CHECK=1       跳过 root 检查
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_BASE="virtual-life"
UNIT_DIRS="${SYSTEMD_UNIT_DIRS:-/etc/systemd/system /usr/lib/systemd/system /lib/systemd/system}"
WITH_BACKUP=0
RESTART=1
PY=""
VENV=""

say() { printf '%s\n' "$*"; }
die() { printf '!! %s\n' "$*" >&2; exit 1; }

# ---------- Python 检测 ----------
pick_python() {
  local c v maj min
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    v=$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || continue
    maj=${v%%.*}; min=${v#*.}; min=${min%%.*}
    if [ "$maj" -gt 3 ] || { [ "$maj" -eq 3 ] && [ "$min" -ge 10 ]; }; then
      PY="$c"
      return 0
    fi
  done
  return 1
}

ensure_python() {
  if pick_python; then
    say "Python: $PY ($($PY --version 2>&1))"
    return 0
  fi
  say "未找到 Python 3.10+，尝试 apt 安装 python3..."
  command -v apt-get >/dev/null 2>&1 || die "没有 apt-get；请手动安装 Python 3.10+ 后重跑"
  apt-get update -qq 2>/dev/null || true
  apt-get install -y python3 python3-venv python3-pip \
    || die "apt 安装失败；请手动安装 Python 3.10+（Debian: apt install python3 python3-venv python3-pip）"
  pick_python || die "仍找不到 Python 3.10+"
}

# ---------- venv / 依赖 ----------
create_venv() {
  rm -rf "$VENV"
  say "创建 $VENV（$PY）..."
  if ! "$PY" -m venv "$VENV"; then
    # ensurepip 缺失（Debian/Ubuntu 常见）→ 装对应 python3.X-venv 后重试
    rm -rf "$VENV"
    say "venv 创建失败（通常缺 ensurepip），尝试安装 ${PY}-venv / python3-venv..."
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq 2>/dev/null || true
      apt-get install -y "${PY}-venv" \
        || apt-get install -y python3-venv \
        || die "无法安装 venv 支持包（${PY}-venv / python3-venv）；请手动: apt install ${PY}-venv"
    else
      die "无法自动安装 venv 支持包；请手动: apt install ${PY}-venv（或 python3-venv）"
    fi
    "$PY" -m venv "$VENV" || die "再次创建 venv 失败；请手动: $PY -m venv $VENV"
  fi
}

ensure_venv() {
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/pip" ]; then
    VENV="$VIRTUAL_ENV"
    say "使用已激活的 venv: $VENV"
  else
    VENV="$APP_DIR/.venv"
    # 以 bin/pip 是否可用判断 venv 是否完整；半成品（有 python 无 pip）也重建
    if [ ! -x "$VENV/bin/pip" ]; then
      create_venv
    fi
  fi
  say "安装依赖（requirements.txt）..."
  "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"
}

# ---------- .env ----------
ensure_env() {
  if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
      cp "$APP_DIR/.env.example" "$APP_DIR/.env"
      say "!! 已从 .env.example 生成 .env，请填入 OPENROUTER_API_KEY / APP_AUTH_USERNAME / APP_AUTH_PASSWORD 后重启服务"
    else
      say "!! 缺少 .env（需要 OPENROUTER_API_KEY / APP_AUTH_USERNAME / APP_AUTH_PASSWORD）"
    fi
  else
    grep -q '^OPENROUTER_API_KEY=.\+' "$APP_DIR/.env" \
      || say "!! 警告: .env 里 OPENROUTER_API_KEY 为空"
  fi
}

# ---------- systemd 服务（名字防撞） ----------
unit_file() { # name -> path
  local d
  for d in $UNIT_DIRS; do
    [ -f "$d/$1.service" ] && { echo "$d/$1.service"; return 0; }
  done
  return 1
}

pick_service_name() { # base -> name（幂等：已存在且指向本目录则复用）
  local base="$1" name="$1" f i=2
  if f=$(unit_file "$name"); then
    if grep -q "WorkingDirectory=$APP_DIR" "$f"; then
      echo "$name"; return 0
    fi
    while f=$(unit_file "$name"); do
      name="${base}-$i"; i=$((i+1))
    done
  fi
  echo "$name"
}

install_service() { # name -> unit_path
  local name="$1"
  local dir; dir=$(printf '%s\n' $UNIT_DIRS | awk 'NF{print $1}')
  local unit="$dir/$name.service"
  cat > "$unit" << EOF
[Unit]
Description=Virtual Life (roleplay chat with memory)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$VENV/bin/python $APP_DIR/main.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
  echo "$unit"
}

# ---------- 主流程 ----------
main() {
  if [ "${SKIP_ROOT_CHECK:-0}" != "1" ] && [ "$(id -u)" != 0 ]; then
    local d; d=$(printf '%s\n' $UNIT_DIRS | awk 'NF{print $1}')
    case "$d" in
      /etc/systemd/*|/usr/lib/systemd/*|/lib/systemd/*) die "请用 root 运行（目标单元目录是系统目录 $d）" ;;
    esac
  fi
  command -v systemctl >/dev/null 2>&1 || die "未找到 systemctl（本机不是 systemd 环境？）"

  say "== 1/4 环境检测/创建 =="
  ensure_python
  ensure_venv
  ensure_env

  say "== 2/4 systemd 服务（名字防撞） =="
  local NAME UNIT
  NAME=$(pick_service_name "$SERVICE_BASE")
  UNIT=$(install_service "$NAME")
  say "服务: $NAME ($UNIT)"
  say "解释器: $VENV/bin/python"
  systemctl daemon-reload
  systemctl enable "$NAME"
  if [ "$RESTART" = 1 ]; then
    systemctl restart "$NAME"
    sleep 2
    systemctl status "$NAME" --no-pager | head -12 || true
  fi

  say "== 3/4 完成 =="
  local ip; ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
  say "  服务: $NAME (systemctl status $NAME)"
  say "  访问: http://${ip:-<服务器IP>}:7861"

  say "== 4/4 备份 =="
  if [ "$WITH_BACKUP" = 1 ]; then
    bash "$SCRIPT_DIR/backup.sh" setup --mode local || true
  else
    say "  需要备份请运行: bash $SCRIPT_DIR/backup.sh setup"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --with-backup) WITH_BACKUP=1; shift ;;
    --no-restart)  RESTART=0;   shift ;;
    -h|--help)     say "用法: $0 [--with-backup] [--no-restart]"; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

main
