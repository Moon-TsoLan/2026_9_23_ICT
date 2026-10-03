#!/usr/bin/env bash
# Read-only survey of the instance before installing anything. Changes nothing.

echo "== identity"
hostname
uname -a
nproc
echo
echo "== memory / disk"
free -g | head -2
df -h / /root 2>/dev/null | tail -3
echo
echo "== gpu"
nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv,noheader 2>&1 | head -3
echo
echo "== python stack"
command -v python3 && python3 -V
command -v conda && conda env list
python3 -c "import sys; print(sys.executable)"
echo
echo "== already installed"
for m in paddle paddleocr paddlex fastapi uvicorn pypdfium2 bs4 fitz cv2; do
  python3 -c "import $m; v=getattr($m,'__version__','?'); print('$m', v)" 2>/dev/null || echo "$m MISSING"
done
echo
echo "== converters and fonts"
for b in soffice libreoffice unoconv pdftoppm gs; do
  printf "%-12s " "$b"
  command -v $b || echo "- absent"
done
fc-list 2>/dev/null | grep -ciE "cjk|wqy|noto sans cjk|source han"
echo
echo "== running services"
ps -eo pid,pcpu,pmem,etime,args --sort=-pcpu 2>/dev/null | head -6 | cut -c1-150
echo
echo "== listening ports"
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | head -12
echo
echo "== model dirs"
ls -la /root 2>/dev/null | head -14
du -sh /root/model 2>/dev/null
ls /root/model 2>/dev/null | head
echo
echo "== outbound network"
curl -s -m 8 -o /dev/null -w "pypi:%{http_code}\n" https://pypi.org/simple/ || echo "pypi unreachable"
curl -s -m 8 -o /dev/null -w "hf:%{http_code}\n" https://huggingface.co || echo "hf unreachable"
test -f /etc/network_turbo && echo "network_turbo available"
echo
echo "== vllm from inside"
curl -s -m 8 http://127.0.0.1:6006/v1/models | head -c 300
echo
echo "== done"
