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
#   SYSTEMD_UNIT_DIR=...    自定义单元目录（默认 /etc/systemd/system；兼容旧的 SYSTEMD_UNIT_DIRS，取第一项）
#   SKIP_ROOT_CHECK=1       跳过 root 检查
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_BASE="virtual-life"
# 单元目录：统一用单个目录（默认 /etc/systemd/system，admin 单元优先级最高）。
# 搜索 / 防撞 / 写入全部用同一个 UNIT_DIR，避免“查得到却写不进去”的目录不一致。
if [ -n "${SYSTEMD_UNIT_DIR:-}" ]; then
  UNIT_DIR="$SYSTEMD_UNIT_DIR"
else
  UNIT_DIR=$(printf '%s\n' ${SYSTEMD_UNIT_DIRS:-/etc/systemd/system} | awk 'NF{print $1}')
fi
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
uv_ready() {
  command -v uv >/dev/null 2>&1 || [ -x /usr/local/bin/uv ] || [ -x "$APP_DIR/.uv/uv" ]
}

ensure_uv() {
  # 已有 uv（PATH 或 /usr/local/bin）直接用；否则下载静态二进制
  UV=""
  if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"; return 0; fi
  if [ -x /usr/local/bin/uv ]; then UV=/usr/local/bin/uv; return 0; fi

  local arch os url tmp
  case "$(uname -m)" in
    x86_64|amd64) arch=x86_64 ;;
    aarch64|arm64) arch=aarch64 ;;
    *) say "!! 未知架构 $(uname -m)，无法下载 uv"; return 1 ;;
  esac
  case "$(uname -s)" in
    Linux) os=unknown-linux-gnu ;;
    Darwin) os=apple-darwin ;;
    *) say "!! 未知系统 $(uname -s)，无法下载 uv"; return 1 ;;
  esac
  url="https://github.com/astral-sh/uv/releases/latest/download/uv-${arch}-${os}.tar.gz"
  say "下载 uv（$url）..."
  tmp=$(mktemp -d)
  if ! curl -fsSL --max-time 120 "$url" -o "$tmp/uv.tar.gz" \
     || ! tar -xzf "$tmp/uv.tar.gz" -C "$tmp" \
     || [ ! -f "$tmp/uv-${arch}-${os}/uv" ]; then
    rm -rf "$tmp"
    say "!! 下载 uv 失败（需要外网访问 github.com）"
    return 1
  fi
  if [ -w /usr/local/bin ]; then
    cp "$tmp/uv-${arch}-${os}/uv" /usr/local/bin/uv
    chmod +x /usr/local/bin/uv
    UV=/usr/local/bin/uv
  else
    mkdir -p "$APP_DIR/.uv"
    cp "$tmp/uv-${arch}-${os}/uv" "$APP_DIR/.uv/uv"
    chmod +x "$APP_DIR/.uv/uv"
    UV="$APP_DIR/.uv/uv"
  fi
  rm -rf "$tmp"
  say "uv 就绪: $UV"
}

create_venv() {
  rm -rf "$VENV"
  say "创建 $VENV（$PY）..."
  if "$PY" -m venv "$VENV" 2>/dev/null; then
    return 0
  fi

  # 第 1 级兜底：下载 uv（静态二进制，自带 venv/pip，完全绕开 ensurepip）
  rm -rf "$VENV"
  say "python -m venv 失败（通常缺 ensurepip）。下载 uv 创建环境..."
  if ensure_uv; then
    if ! "$UV" venv "$VENV" --python "$PY"; then
      rm -rf "$VENV"
      die "uv venv 失败；请手动检查 $PY"
    fi
    say "venv 已由 uv 创建"
    return 0
  fi

  # 第 2 级兜底：apt 安装 python3.X-venv 后重试
  # （python3.13-venv 在 Debian 13 上存在；装不上多半是 apt 索引过期，先 apt-get update）
  say "尝试安装 ${PY}-venv / python3-venv 后用 python -m venv 重试..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq || true
    apt-get install -y "${PY}-venv" 2>/dev/null \
      || apt-get install -y python3-venv 2>/dev/null \
      || say "!! apt 装不上 ${PY}-venv / python3-venv（索引可能过期，先手动跑 apt-get update）"
  else
    say "无 apt-get，跳过装包"
  fi
  if "$PY" -m venv "$VENV" 2>/dev/null; then
    say "venv 创建成功（安装 venv 支持包后）"
    return 0
  fi

  # 第 3 级兜底：--without-pip + get-pip.py（Debian stripped python 的经典解法）
  rm -rf "$VENV"
  say "仍失败，改用 --without-pip + get-pip.py 引导 pip..."
  "$PY" -m venv --without-pip "$VENV" || die "连 --without-pip 都失败；请手动检查 $PY 是否正常"
  local gpfile
  gpfile="/tmp/vl-get-pip-$$.py"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --max-time 120 https://bootstrap.pypa.io/get-pip.py -o "$gpfile" || die "下载 get-pip.py 失败（需要外网）"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$gpfile" --timeout=120 https://bootstrap.pypa.io/get-pip.py || die "下载 get-pip.py 失败（需要外网）"
  else
    die "没有 curl/wget，无法引导 pip；请手动: apt-get update && apt install ${PY}-venv"
  fi
  "$VENV/bin/python" "$gpfile" || die "pip 引导失败（请检查外网/PyPI 连通性）"
  rm -f "$gpfile"
  say "pip 已通过 get-pip.py 引导"
}

ensure_venv() {
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    VENV="$VIRTUAL_ENV"
    say "使用已激活的 venv: $VENV"
  else
    VENV="$APP_DIR/.venv"
    # venv 完整 = bin/python 可执行，且（有 pip 或有 uv 可代替 pip）
    # 半成品（有 python 无 pip、又无 uv）也重建
    if [ ! -x "$VENV/bin/python" ] || { [ ! -x "$VENV/bin/pip" ] && ! uv_ready; }; then
      create_venv
    fi
  fi
  install_deps
}

install_deps() {
  if [ -n "${UV:-}" ] && [ -x "$UV" ]; then
    say "安装依赖（uv，requirements.txt）..."
    "$UV" pip install --python "$VENV/bin/python" -r "$APP_DIR/requirements.txt"
  else
    say "安装依赖（pip，requirements.txt）..."
    "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"
  fi
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
unit_file() { # name -> path（只在本目录内查找，与写入目录一致）
  [ -f "$UNIT_DIR/$1.service" ] && { echo "$UNIT_DIR/$1.service"; return 0; }
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
  mkdir -p "$UNIT_DIR"
  local unit="$UNIT_DIR/$name.service"
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
    case "$UNIT_DIR" in
      /etc/systemd/*|/usr/lib/systemd/*|/lib/systemd/*) die "请用 root 运行（目标单元目录是系统目录 $UNIT_DIR）" ;;
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
