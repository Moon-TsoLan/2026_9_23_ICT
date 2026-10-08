#!/usr/bin/env bash
# Ubuntu 24.04 应用机一键准备：装依赖 → 建目录 → 建虚拟环境 → 装 Python 包。
# 幂等，可重复执行（已经装过的会跳过/覆盖）。
#
#   sudo bash deploy/install_app.sh
#
# 不动代码、不碰 .env、不起服务；这些分别在《生产部署.md》的第 2、6、8 步做。
set -euo pipefail

REPO=${REPO:-/root/workspace/ICT}
DATA=${DATA:-/root/workspace/ICT/var}
VENV="$REPO/.venv"

echo "== 1/5 apt 依赖 =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
# git：拉代码用；p7zip-full：只有附件里出现内层 rar/7z 才需要
apt-get install -y python3-venv python3-pip git p7zip-full

echo "== 2/5 目录 =="
# 运行数据放独立盘；后端与 worker 的 ICT_DATA_ROOT 都指这里
mkdir -p "$DATA" "$REPO/data"      # data/ 放 procurement_catalog_2022.json（git 里没有）
if [ ! -d "$REPO" ]; then
  echo "!! $REPO 不存在：请先把代码放进去（git clone 或 rsync），再重跑本脚本"
  exit 1
fi

echo "== 3/5 虚拟环境 =="
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip wheel

echo "== 4/5 主流程依赖（pyproject） =="
"$VENV/bin/pip" install -e "$REPO"

echo "== 5/5 后端 / 隧道等额外依赖 =="
# pyproject 只声明主流程；后端四个包 + multipart + 隧道用的 paramiko 要单独装
"$VENV/bin/pip" install \
  fastapi "uvicorn[standard]" "psycopg[binary]" psycopg-pool python-multipart paramiko

echo
echo "完成。接下来按《生产部署.md》："
echo "  第 2 步 放 .env 与 data/procurement_catalog_2022.json"
echo "  第 3 步 docker compose up -d"
echo "  第 5 步 npm ci && npm run build"
echo "  第 6 步 装 systemd 三个服务"
echo "  第 7 步 配 nginx"
