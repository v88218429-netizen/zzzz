#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

clear
printf '\n'
printf '╭──────────────────────────────────────────────────────────────╮\n'
printf '│                      WB AI MANAGER                           │\n'
printf '│              Центр управления · только чтение                       │\n'
printf '╰──────────────────────────────────────────────────────────────╯\n'
printf '\n'
printf '  Защита включена: любые изменения в Wildberries заблокированы.\n'
printf '  Внешний API модели для запуска не требуется.\n'
printf '  Автообновление: включено · правила ~60 сек · код ~60 сек с откатом.\n\n'

if [ ! -x .venv/bin/python ]; then
  echo 'Первый запуск: создаю локальное окружение…'
  echo 'Понадобится интернет только для установки Python-библиотек.'
  echo
  ./scripts/bootstrap_macos.sh
  echo
fi

cat <<'TXT'
Что открыть?

  1  АВТО — мой кабинет: источники + веб + решения · только чтение
  2  Демо — посмотреть интерфейс без токенов
  3  Мой Wildberries — старый режим только чтение
  4  Проверка защиты и тестов
  5  Подключения — WB token / Google Sheets
  0  Выход

TXT
printf 'Выбор [1]: '
IFS= read -r CHOICE
CHOICE=${CHOICE:-1}

case "$CHOICE" in
  1) exec ./scripts/launch/auto_readonly.command ;;
  2) exec ./scripts/launch/demo.command ;;
  3) exec ./scripts/launch/live_readonly.command ;;
  4) exec ./scripts/launch/safety_tests.command ;;
  5) exec ./scripts/launch/configure_connections.command ;;
  0) exit 0 ;;
  *) echo 'Неизвестный вариант.'; sleep 2; exec "$0" ;;
esac
