#!/usr/bin/env bash
# Ставит летописца: команду `chronicle`, навыки /work и /report для Claude Code, таймер раз в 15 минут.
#   ./install.sh              установить или обновить
#   ./install.sh --uninstall  удалить (база с историей остаётся в ~/.local/share/claude-chronicle)
#   CHRONICLE_NO_TIMER=1 ./install.sh   без таймера (запускать `chronicle run` самому)
#   CHRONICLE_INSTALL_ROOT=/tmp/chronicle ./install.sh   изолированная установка без таймера
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3)" || { echo "Нужен Python 3.10 или новее"; exit 1; }
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "Нужен Python 3.10 или новее"; exit 1; }
PYTHON="$("$PYTHON" -c 'import sys; print(sys.executable)')"
ROOT="${CHRONICLE_INSTALL_ROOT:-$HOME}"
ROOT="$("$PYTHON" -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$ROOT")"
DATA_HOME="${CHRONICLE_HOME:-$ROOT/.local/share/claude-chronicle}"
DATA_HOME="$("$PYTHON" -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$DATA_HOME")"
APP="$DATA_HOME/app"
STATE="$DATA_HOME/install-state"
BIN="$ROOT/.local/bin/chronicle"
SKILLS="$ROOT/.claude/skills"
UNIT="$ROOT/.config/systemd/user"
PLIST="$ROOT/Library/LaunchAgents/com.claude-chronicle.plist"
# Изолированный префикс никогда не меняет службы настоящего пользователя.
ISOLATED=0
if [ "$ROOT" != "$HOME" ]; then ISOLATED=1; fi

owned_skill() {
  [ -f "$SKILLS/$1/.claude-chronicle-owned" ] &&
    [ "$(cat "$SKILLS/$1/.claude-chronicle-owned")" = "claude-chronicle" ]
}

if [ "${1:-}" = "--uninstall" ]; then
  if [ "$ISOLATED" = 0 ] && command -v systemctl >/dev/null && [ -f "$UNIT/claude-chronicle.timer" ]; then
    systemctl --user disable --now claude-chronicle.timer || true
    rm -f "$UNIT/claude-chronicle.timer" "$UNIT/claude-chronicle.service"
    systemctl --user daemon-reload
  fi
  if [ "$ISOLATED" = 0 ] && [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
  fi
  for s in work report; do
    if owned_skill "$s"; then
      if cmp -s "$SKILLS/$s/SKILL.md" "$STATE/$s.md"; then
        rm -f "$SKILLS/$s/SKILL.md" "$SKILLS/$s/.claude-chronicle-owned"
        rmdir "$SKILLS/$s" 2>/dev/null || true
      else
        echo "Сохранён изменённый навык $SKILLS/$s."
      fi
    fi
    if [ -e "$STATE/previous-skills/$s" ]; then
      if [ ! -e "$SKILLS/$s" ]; then
        mv "$STATE/previous-skills/$s" "$SKILLS/$s"
      else
        echo "Прежний навык сохранён в $STATE/previous-skills/$s."
      fi
    fi
  done
  rm -rf "$APP"
  if [ -f "$BIN" ] && grep -qx '# Installed by claude-chronicle' "$BIN"; then rm -f "$BIN"; fi
  echo "Удалено. История осталась в ${APP%/app} — удали папку сам, если она не нужна."
  exit 0
fi

command -v claude >/dev/null || echo "⚠ Не нашёл команду claude. Пересказы и отчёты работают через Claude Code — поставь его."

# Не затереть более раннюю резервную копию, если человек уже заменил наш навык.
for s in work report; do
  if owned_skill "$s" && ! cmp -s "$SKILLS/$s/SKILL.md" "$STATE/$s.md"; then
    echo "Остановлено: установленный навык $SKILLS/$s изменён. Сохрани его отдельно перед обновлением."
    exit 1
  fi
  if [ -e "$SKILLS/$s" ] && ! owned_skill "$s" && [ -e "$STATE/previous-skills/$s" ]; then
    echo "Остановлено: $SKILLS/$s уже занят, а его прежняя версия хранится в $STATE/previous-skills/$s."
    exit 1
  fi
done

# 1. Программа и команда chronicle (только стандартная библиотека Python — ставить нечего)
mkdir -p "$APP" "$(dirname "$BIN")" "$STATE/previous-skills"
rm -rf "$APP/chronicle" && cp -r "$SRC/chronicle" "$APP/"
"$PYTHON" - "$BIN" "$APP" "$DATA_HOME" "$PYTHON" "${CLAUDE_HOME:-$ROOT/.claude}" <<'PY'
from pathlib import Path
import shlex
import sys

target, app, data, python, claude_home = sys.argv[1:]
Path(target).write_text(
    '#!/usr/bin/env bash\n# Installed by claude-chronicle\n'
    'if [ -z "${CHRONICLE_HOME:-}" ]; then export CHRONICLE_HOME=' + shlex.quote(data) + '; fi\n'
    'if [ -z "${CLAUDE_HOME:-}" ]; then export CLAUDE_HOME=' + shlex.quote(claude_home) + '; fi\n'
    'PYTHONPATH=' + shlex.quote(app) + ' exec ' + shlex.quote(python) + ' -m chronicle "$@"\n'
)
PY
chmod +x "$BIN"

# 2. Навыки /work и /report; прежние версии сохраняются в install-state/previous-skills.
for s in work report; do
  if [ -e "$SKILLS/$s" ] && ! owned_skill "$s"; then
    mv "$SKILLS/$s" "$STATE/previous-skills/$s"
  fi
  mkdir -p "$SKILLS/$s" && cp "$SRC/skills/$s/SKILL.md" "$SKILLS/$s/SKILL.md"
  printf '%s\n' 'claude-chronicle' > "$SKILLS/$s/.claude-chronicle-owned"
  cp "$SRC/skills/$s/SKILL.md" "$STATE/$s.md"
done

# 3. Таймер: раз в 15 минут дочитывает журналы и разбирает новое
CLAUDE_DIR="$(dirname "$(command -v claude 2>/dev/null || echo /usr/local/bin/claude)")"
if [ "$ISOLATED" = 1 ]; then
  TIMER="не ставил (CHRONICLE_INSTALL_ROOT)"
elif [ -n "${CHRONICLE_NO_TIMER:-}" ]; then
  TIMER="не ставил (CHRONICLE_NO_TIMER)"
elif command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$UNIT"
  cat > "$UNIT/claude-chronicle.service" <<EOF
[Unit]
Description=Летописец сессий Claude Code

[Service]
Type=oneshot
Environment="PATH=$CLAUDE_DIR:$ROOT/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart="$BIN" run
TimeoutStartSec=20min
EOF
  cat > "$UNIT/claude-chronicle.timer" <<EOF
[Unit]
Description=Летописец сессий Claude Code раз в 15 минут

[Timer]
OnBootSec=3min
OnUnitInactiveSec=15min

[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now claude-chronicle.timer
  TIMER="systemd (claude-chronicle.timer)"
elif [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.claude-chronicle</string>
  <key>ProgramArguments</key><array><string>$BIN</string><string>run</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$CLAUDE_DIR:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  TIMER="launchd (com.claude-chronicle)"
else
  TIMER="нет — добавь в crontab: */15 * * * * $BIN run"
fi

# 4. Первый сбор журналов (без модели, секунды)
"$BIN" run --no-llm >/dev/null

echo "Готово."
echo "  команда:  $BIN   (если не находится — добавь ~/.local/bin в PATH)"
echo "  навыки:   /work и /report в любой сессии Claude Code"
echo "  таймер:   $TIMER"
echo "Первый разбор истории моделью идёт по таймеру; сразу целиком: chronicle run --all"
