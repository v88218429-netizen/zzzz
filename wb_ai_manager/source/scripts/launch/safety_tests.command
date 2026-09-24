#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
  ./scripts/bootstrap_macos.sh
fi
source .venv/bin/activate
python -m pytest -q || {
  echo "pytest не установлен; ставлю dev-зависимости..."
  pip install -e '.[dev]'
  python -m pytest -q
}
echo
echo "Если все тесты passed — safety + decision-тесты, включая блок 200 000 ₽ ставки, прошли."
read -r -p "Нажми Enter, чтобы закрыть..." _
