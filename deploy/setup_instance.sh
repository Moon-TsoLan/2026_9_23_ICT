#!/usr/bin/env bash
# Deploy the parsing stack on THIS instance (3080 Ti, 10 vCPU, 45 GB RAM, ~11 GB free disk).
# Versus install_parse.sh: reuse conda base python, CPU-only paddle (vLLM holds the GPU),
# LibreOffice writer only, weights from an uploaded tar, disk guard before every phase.
set -euo pipefail

PY=${PY:-/root/miniconda3/bin/python}
PIP="$PY -m pip --disable-pip-version-check --no-input"
WHEEL_INDEX=${WHEEL_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}
PDX=${PDX_MODELS:-/root/pdx_models}
SVC=${SERVICE_DIR:-/opt/ict-parse}
VLLM=${ICT_VL_SERVER_URL:-http://127.0.0.1:6006/v1}

free_gb() { df -BG --output=avail / | tail -1 | tr -dc 0-9; }
step() { echo "[$(date +%H:%M:%S)] $*"; }
guard() {
  need=$1
  now=$(free_gb)
  step "disk free=${now}G need>=${need}G"
  if [ "$now" -lt "$need" ]; then echo "ABORT: disk below ${need}G"; exit 1; fi
}

step "setup start"
guard 5

step "1/5 apt: LibreOffice writer only + CJK fonts (missing fonts reflow table cells)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends libreoffice-writer fonts-noto-cjk fonts-wqy-zenhei
apt-get clean
rm -rf /var/lib/apt/lists/*
command -v soffice
guard 4

step "2/5 pip: CPU paddle plus paddleocr/paddlex pinned to the versions proven on Windows"
$PIP install -i "$WHEEL_INDEX" "paddlepaddle==3.2.1"
$PIP install -i "$WHEEL_INDEX" "paddleocr==3.7.0" "paddlex==3.7.2" "fastapi>=0.115" "uvicorn[standard]>=0.30" "pypdfium2>=4.30" "beautifulsoup4>=4.12" "lxml>=5.0"
guard 3

step "3/5 weights: PP-DocLayoutV3 from the uploaded tar (VL weights stay inside vLLM)"
mkdir -p "$PDX/official_models"
if [ -f /root/PP-DocLayoutV3.tar.gz ]; then
  tar -xzf /root/PP-DocLayoutV3.tar.gz -C "$PDX/official_models"
else
  echo "WARN /root/PP-DocLayoutV3.tar.gz missing - first run would download it"
fi
ls -1 "$PDX/official_models" || true

step "4/5 env file for the service"
mkdir -p "$SVC"
cat > "$SVC/parse.env" <<EOF
DEVICE=cpu
PADDLE_PDX_CACHE_HOME=$PDX
PADDLE_PDX_MODEL_SOURCE=huggingface
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
HF_HUB_DISABLE_XET=1
ICT_VL_SERVER_URL=$VLLM
ICT_VL_API_MODEL_NAME=PaddlePaddle/PaddleOCR-VL-1.6
ICT_PARSE_MAX_CONCURRENCY=16
ICT_PARSE_FILE_WORKERS=4
ICT_PARSE_PORT=6008
ICT_SOFFICE_TIMEOUT=180
EOF
echo "wrote $SVC/parse.env"

step "5/5 versions"
$PY -c "import paddle; print(paddle.__version__)"
$PY -c "import paddleocr, paddlex; print(paddleocr.__version__, paddlex.__version__)"
$PY -c "import pypdfium2, bs4, fastapi, uvicorn; print(1)" || true
guard 2
step "setup done"
