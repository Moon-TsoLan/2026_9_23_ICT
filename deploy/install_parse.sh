#!/usr/bin/env bash
# Install the document-parsing stack on the GPU instance.
# Layout detection runs here (CPU by default); recognition stays on the existing vLLM server.
# Idempotent: adds pip packages plus files under /opt/ict-parse only.

set -euo pipefail

DEVICE="${DEVICE:-cpu}"                     # cpu | gpu
PIP_INDEX="${PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PADDLE_CPU_INDEX="https://www.paddlepaddle.org.cn/packages/stable/cpu"
PADDLE_GPU_INDEX="https://www.paddlepaddle.org.cn/packages/stable/cu118"
PDX_MODELS="${PDX_MODELS:-/root/pdx_models}"
SERVICE_DIR="${SERVICE_DIR:-/opt/ict-parse}"
VLLM_PORT="${VLLM_PORT:-6006}"
PARSE_PORT="${PARSE_PORT:-6008}"

echo "== 1/5  system: LibreOffice so doc/docx/xls become pdf, CJK fonts so cells do not reflow wrong"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libreoffice fonts-noto-cjk fonts-wqy-zenhei fonts-wqy-microhei procps curl
fi
command -v soffice >/dev/null 2>&1 || echo "WARN: soffice missing, office conversion will fail"

echo "== 2/5  python: pinned to the versions that work locally (paddleocr 3.7.0 / paddlex 3.7.2)"
python3 -V
if [ "$DEVICE" = "gpu" ]; then
  python3 -m pip install --index-url "$PADDLE_GPU_INDEX" "paddlepaddle-gpu==3.2.1"
else
  python3 -m pip install --index-url "$PADDLE_CPU_INDEX" "paddlepaddle==3.2.1"
fi
python3 -m pip install --index-url "$PIP_INDEX" \
  "paddleocr==3.7.0" "paddlex==3.7.2" "fastapi>=0.115" "uvicorn[standard]>=0.30" \
  "pypdfium2>=4.30" "beautifulsoup4>=4.12" "lxml>=5.0"
python3 -c "import paddle, paddleocr, paddlex; print('paddle', paddle.__version__, '| paddleocr', paddleocr.__version__, '| paddlex', paddlex.__version__)"

echo "== 3/5  models: PP-DocLayoutV3 runs here, PaddleOCR-VL-1.6 recognition stays in vLLM"
mkdir -p "$PDX_MODELS/official_models"
for m in PaddleOCR-VL-1.6 PP-DocLayoutV3; do
  if [ -d "$PDX_MODELS/official_models/$m" ]; then
    echo "ok   $m"
  else
    echo "MISS $m -> first run downloads it, or copy the bundle already on Windows:"
    echo "     scp -r 'D:/PaddleOCR-VL-5060/models/official_models/$m' root@HOST:$PDX_MODELS/official_models/"
  fi
done

echo "== 4/5  env file"
mkdir -p "$SERVICE_DIR"
cat >"$SERVICE_DIR/parse.env" <<EOF
DEVICE=${DEVICE}
PADDLE_PDX_CACHE_HOME=$PDX_MODELS
PADDLE_PDX_MODEL_SOURCE=huggingface
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
HF_HUB_DISABLE_XET=1
ICT_VL_SERVER_URL=http://127.0.0.1:$VLLM_PORT/v1
ICT_VL_API_MODEL_NAME=PaddlePaddle/PaddleOCR-VL-1.6
ICT_PARSE_MAX_CONCURRENCY=16
ICT_PARSE_PORT=$PARSE_PORT
ICT_SOFFICE_TIMEOUT=180
EOF
echo "wrote $SERVICE_DIR/parse.env"

echo "== 5/5  next steps (run from this deploy/ directory; send me probe_report.json)"
cat <<EOF
  set -a; . $SERVICE_DIR/parse.env; set +a
  python3 probe_paddlex.py --file '/root/???????????([2]).pdf' --pages 1-6 --out /root/probe_report.json
  python3 -m uvicorn parse_service:app --host 0.0.0.0 --port $PARSE_PORT
EOF
