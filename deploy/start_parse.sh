#!/usr/bin/env bash
# Restart the parsing service (6008). Kept in the repo because the instance's copy was the
# only one and rebuilding the instance lost it once.
#
# Concurrency comes from two sides and they multiply:
#   ICT_PARSE_MAX_INFLIGHT  (客户端)  本地 ParseQueue 同时往 GPU 送几份文档
#   --workers               (服务端)  uvicorn 进程数；每个进程又有自己的 ICT_PARSE_FILE_WORKERS
# 实测（2026-10-07，3080 Ti，batch wall）：N=3 / --workers 4 比 N=1 / --workers 2 的基准快 41%；
# N=3/8 与 N=2/8 没有再变快。两边要一起调。
set -uo pipefail

PORT=${ICT_PARSE_PORT:-6008}
BIND=${PARSE_BIND:-127.0.0.1}
WORKERS=${PARSE_WORKERS:-4}
LOG=${LOG:-/root/parse_service.log}

set -a; . /opt/ict-parse/parse.env; set +a
cd /root/deploy

old=$(pgrep -f "uvicorn parse_service:app" || true)
if [ -n "${old:-}" ]; then
  echo "stopping existing parse service pids=$old"
  kill $old 2>/dev/null || true
  for _ in $(seq 1 15); do
    pgrep -f "uvicorn parse_service:app" >/dev/null || break
    sleep 1
  done
  if pgrep -f "uvicorn parse_service:app" >/dev/null; then
    echo "refusing to start a second service: old process still alive"
    exit 1
  fi
fi

nohup /root/miniconda3/bin/python -m uvicorn parse_service:app \
  --host "$BIND" --port "$PORT" --workers "$WORKERS" > "$LOG" 2>&1 &
echo "parse launcher pid=$! workers=$WORKERS log=$LOG"

for i in $(seq 1 60); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/health" | grep -q '"ok"'; then
    echo "parse ready after ${i}s"
    exit 0
  fi
  sleep 1
done
echo "parse NOT ready within 60s; last log lines:"
tail -20 "$LOG"
exit 1
