#!/bin/bash
set -euo pipefail

REPO="v88218429-netizen/zzzz"
RAW="https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg"
RUNNER_DIR="$HOME/actions-runner-ozon"
WORK="$HOME/.ozon-radar-bootstrap"
mkdir -p "$WORK"

echo "OZON · REPAIR MAC RUNNER"
echo "========================"

runner_status() {
  if [ -x "$RUNNER_DIR/svc.sh" ]; then
    (cd "$RUNNER_DIR" && ./svc.sh status) || true
  fi
}

if [ -f "$RUNNER_DIR/.runner" ] && [ -x "$RUNNER_DIR/svc.sh" ]; then
  echo "[1/3] Найдена существующая регистрация GitHub runner."
  cd "$RUNNER_DIR"
  ./svc.sh stop >/dev/null 2>&1 || true
  ./svc.sh install >/dev/null 2>&1 || true
  ./svc.sh start
  sleep 3
  ./svc.sh status || true
  cd - >/dev/null
else
  echo "[1/3] Локальная регистрация runner не найдена."
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    echo "[2/3] Получаю одноразовый registration token через текущий GitHub login..."
    TOKEN="$(gh api --method POST "repos/$REPO/actions/runners/registration-token" --jq .token 2>/dev/null || true)"
    if [ -n "$TOKEN" ]; then
      curl -fsSL "$RAW/ozon_live/setup_self_hosted_mac.sh" -o "$WORK/setup_self_hosted_mac.sh"
      chmod +x "$WORK/setup_self_hosted_mac.sh"
      bash "$WORK/setup_self_hosted_mac.sh" "$TOKEN"
    else
      echo "⚠️ Не удалось получить runner token через gh."
      echo "LIVE radar всё равно можно запустить напрямую через launchd; GitHub jobs останутся queued."
    fi
  else
    echo "⚠️ GitHub CLI не авторизован или не установлен."
    echo "LIVE radar всё равно можно запустить напрямую через launchd; GitHub jobs останутся queued."
  fi
fi

echo "[3/3] Текущее состояние:"
runner_status

echo
echo "Runner repair завершён."
