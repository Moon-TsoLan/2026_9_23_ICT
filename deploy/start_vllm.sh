#!/usr/bin/env bash
# Restart the PaddleOCR-VL vLLM server exactly as launched by hand, plus one flag.
# paddlex puts per-block min_pixels/max_pixels into mm_processor_kwargs; vLLM rejects that
# unless --trust-request-mm-kwargs is set, so the parsing pipeline needs it enabled.
# Everything else is copied verbatim from /proc/<pid>/cmdline of the running server.
set -uo pipefail

MODEL=${MODEL:-/root/model/PaddleOCR-VL-1.6}
SERVED=${SERVED_MODEL_NAME:-PaddlePaddle/PaddleOCR-VL-1.6}
PORT=${PORT:-6006}
LOG=${LOG:-/root/vllm.log}
ENV_NAME=${ENV_NAME:-paddleocr-vl}

export VLLM_USE_FLASHINFER_SAMPLER=0
source /root/miniconda3/etc/profile.d/conda.sh
conda activate "$ENV_NAME"

old=$(pgrep -f "vllm serve $MODEL" | head -1 || true)
if [ -n "${old:-}" ]; then
  echo "stopping existing vllm pid=$old"
  kill "$old"
  for _ in $(seq 1 40); do
    pgrep -f "vllm serve $MODEL" >/dev/null || break
    sleep 1
  done
  if pgrep -f "vllm serve $MODEL" >/dev/null; then
    echo "refusing to start a second server: old process still alive"
    exit 1
  fi
  echo "old server stopped"
fi

nohup vllm serve "$MODEL" \
  --served-model-name "$SERVED" \
  --trust-remote-code \
  --max-model-len 131072 \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.8 \
  --port "$PORT" \
  --host 0.0.0.0 \
  --trust-request-mm-kwargs \
  >"$LOG" 2>&1 &
echo "starting vllm pid=$! log=$LOG"

for i in $(seq 1 180); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/v1/models" | grep -q "$SERVED"; then
    echo "vllm ready after ${i}s"
    exit 0
  fi
  sleep 1
done
echo "vllm not ready within 180s; last log lines:"
tail -25 "$LOG"
exit 1
