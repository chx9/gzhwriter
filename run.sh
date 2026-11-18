#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Pick a Python >= 3.11. You can override with PYTHON=/path/to/python3.12 ./run.sh
if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
else
  PY=""
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
      major="${version%%.*}"
      minor="${version##*.}"
      if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
        PY="$candidate"
        break
      fi
    fi
  done
fi

if [[ -z "$PY" ]]; then
  echo "需要 Python 3.11+。请安装后重试，或设置 PYTHON=/path/to/python3.11 ./run.sh" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "创建虚拟环境（$PY）..."
  "$PY" -m venv .venv
fi

if ! .venv/bin/python -c 'import fastapi, langgraph, openai' >/dev/null 2>&1; then
  echo "安装后端依赖..."
  .venv/bin/pip install -e '.[dev]'
fi

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  echo "已创建 .env（默认无 Key，使用演示模式）"
fi

if command -v npm >/dev/null 2>&1; then
  need_build=0
  if [[ ! -f web/dist/index.html ]]; then
    need_build=1
  elif find web/src -newer web/dist/index.html -print -quit 2>/dev/null | grep -q .; then
    need_build=1
  elif [[ web/package.json -nt web/dist/index.html ]]; then
    need_build=1
  fi
  if [[ "$need_build" == "1" ]]; then
    echo "构建前端..."
    (cd web && npm install --silent && npm run build)
  fi
else
  echo "警告：没有 npm，跳过前端构建；如果 web/dist 不存在，页面会显示构建提示。" >&2
fi

HOST="${GZH_HOST:-127.0.0.1}"
PORT="${GZH_PORT:-8765}"
echo "启动：http://${HOST}:${PORT}"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
