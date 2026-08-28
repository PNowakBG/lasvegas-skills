#!/usr/bin/env bash
# Instalator stacjonarnego agenta LasVegas (Hermes + skill lv-executor).
# Użycie: curl -fsSL <url>/install.sh | bash -s -- KOD_PAROWANIA
set -euo pipefail

CODE="${1:-}"
HERMES_HOME="$HOME/.hermes"
LV_API_URL_DEFAULT="https://lv.ap2ju.com"

say() { printf '\n\033[1m[lv]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[lv] BŁĄD:\033[0m %s\n' "$*" >&2; exit 1; }

say "Instalator stacjonarnego agenta LasVegas — krok po kroku wszystko zrobi za Ciebie."

# --- 0. Kod parowania -------------------------------------------------------
if [[ -z "$CODE" ]]; then
  say "Nie podano kodu parowania."
  read -r -p "Wklej kod z LasVegas (Podłącz agenta): " CODE
fi
[[ "$CODE" =~ ^[A-Z0-9]{10}$ ]] || die "Kod parowania ma 10 znaków (litery/cyfry, bez 0/O/1/I). Otrzymano: '$CODE'"

# --- 1. Hermes Agent ---------------------------------------------------------
if ! command -v hermes >/dev/null 2>&1; then
  say "Instaluję Hermes Agent (oficjalny installer, może potrwać kilka minut)…"
  echo
  say "⚑ WAŻNE — w trakcie instalacji Hermes zapyta:"
  echo '      „How would you like to set up Hermes?"'
  echo "    → Wybierz:  Quick Setup (Nous Portal)   [pierwsza opcja]"
  echo "      (darmowy login OAuth przez przeglądarkę, zero kluczy API, model i"
  echo "       narzędzia włączone automatycznie — dokładnie tego potrzebuje agent)"
  echo
  echo "    ⚑ Na KAŻDYM kolejnym ekranie wyboru wybieraj opcje LOKALNE:"
  echo "      terminal backend → Local, przeglądarka → local Chrome/ten komputer."
  echo "      Opcje Docker/cloud/sandbox/remote pozbawią agenta Twojego profilu"
  echo "      przeglądarki i logowań do bukmacherów."
  echo
  sleep 2
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v hermes >/dev/null 2>&1 || die "Hermes nie jest dostępny w PATH. Uruchom terminal ponownie i odpal instalator jeszcze raz."
say "Hermes Agent: OK ($(hermes --version 2>/dev/null || echo 'zainstalowany'))"

# --- 1.5. Model LLM — tylko gdy Quick Setup (Nous Portal) go nie skonfigurował --
if ! grep -qE '^(OPENROUTER_API_KEY|NOUS_API_KEY|ANTHROPIC_API_KEY|OPENAI_API_KEY)=' "$HERMES_HOME/.env" 2>/dev/null; then
  # Quick Setup (Nous Portal) konfiguruje model OAuth-em, bez klucza w .env —
  # wykryj jego ślady, żeby nie pytać o klucz, który nie jest potrzebny.
  if ls "$HERMES_HOME"/auth*.json >/dev/null 2>&1 \
     || grep -qiE 'nous' "$HERMES_HOME/config.yaml" 2>/dev/null; then
    say "Model skonfigurowany przez Nous Portal (Quick Setup) — pomijam pytanie o klucz."
  else
    say "Nie widzę skonfigurowanego modelu AI. Dwie drogi:"
    echo "    a) uruchom hermes jeszcze raz i wybierz Quick Setup (Nous Portal), albo"
    echo "    b) wklej klucz OpenRouter: https://openrouter.ai/settings/keys"
    read -r -p "Klucz API (ENTER = pomijam — zakładam, że model masz z Quick Setup): " LLM_KEY
    if [[ -n "$LLM_KEY" ]]; then
      printf '\nOPENROUTER_API_KEY=%s\n' "$LLM_KEY" >> "$HERMES_HOME/.env"
      say "Klucz zapisany do $HERMES_HOME/.env."
    else
      say "Pomijam. Jeśli przy pierwszym biegu agent zgłosi brak modelu — uruchom „hermes model”."
    fi
  fi
fi

# --- 2. Config przeglądarki (real-profile, headed, nagrywanie) ---------------
say "Konfiguruję przeglądarkę agenta (Twój profil, widoczne okno, nagrywanie sesji)…"
python3 - "$HERMES_HOME" <<'PY'
import os, sys, yaml
home = sys.argv[1]
path = os.path.join(home, "config.yaml")
cfg = {}
if os.path.exists(path):
    try:
        cfg = yaml.safe_load(open(path)) or {}
    except Exception:
        cfg = {}
browser = cfg.get("browser") or {}
browser.setdefault("backend", "browser-use")
browser["use_real_profile"] = True
browser["headed"] = True
browser["record_sessions"] = True
browser["real_profile_autoclose"] = True
cfg["browser"] = browser
os.makedirs(home, exist_ok=True)
yaml.safe_dump(cfg, open(path, "w"), sort_keys=False, allow_unicode=True)
print("OK")
PY

# --- 3. Skill lv-executor z tego tapa ---------------------------------------
say "Instaluję skilla lv-executor…"
hermes skills tap add PNowakBG/lasvegas-skills >/dev/null 2>&1 || true
hermes skills install PNowakBG/lasvegas-skills/lv-executor

# --- 4. Kod parowania → token ------------------------------------------------
say "Wymieniam kod parowania na token urządzenia…"
EXCHANGE=$(curl -fsS "$LV_API_URL_DEFAULT/api/executor/pairing-codes/$CODE") \
  || die "Wymiana kodu nieudana — kod mógł wygasnąć (15 min). Wygeneruj nowy w LasVegas."
TOKEN=$(printf '%s' "$EXCHANGE" | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
API_URL=$(printf '%s' "$EXCHANGE" | sed -n 's/.*"apiUrl"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
API_URL="${API_URL:-$LV_API_URL_DEFAULT}"
[[ -n "$TOKEN" ]] || die "Serwer nie zwrócił tokenu. Wygeneruj nowy kod w LasVegas."

ENV_FILE="$HERMES_HOME/.env"
touch "$ENV_FILE"
grep -q '^LV_EXECUTOR_TOKEN=' "$ENV_FILE" \
  && sed -i.bak "s|^LV_EXECUTOR_TOKEN=.*|LV_EXECUTOR_TOKEN=$TOKEN|" "$ENV_FILE" \
  || printf '\nLV_EXECUTOR_TOKEN=%s\nLV_API_URL=%s\n' "$TOKEN" "$API_URL" >> "$ENV_FILE"
say "Token zapisany do $ENV_FILE (plik .env Hermsa — nikt go nie wkleja w czat)."

# --- 5. Autostart cyklu co 5 minut ------------------------------------------
say "Rejestruję uruchamianie agenta co 5 minut + po restarcie komputera…"
if [[ "$(uname)" == "Darwin" ]]; then
  PLIST="$HOME/Library/LaunchAgents/com.lasvegas.lv-executor.plist"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.lasvegas.lv-executor</string>
  <key>ProgramArguments</key><array>
    <string>$HOME/.local/bin/hermes</string>
    <string>chat</string>
    <string>--toolsets</string><string>skills,terminal,browser</string>
    <string>-q</string><string>/lv-executor wykonaj zaległe zlecenia</string>
  </array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HERMES_HOME/lv-executor.log</string>
  <key>StandardErrorPath</key><string>$HERMES_HOME/lv-executor.log</string>
</dict></plist>
EOF
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl load "$PLIST"
  say "macOS: launchd zarejestrowany."
else
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/lv-executor.service" <<EOF
[Unit]
Description=LasVegas executor (Hermes lv-executor cycle)

[Service]
Type=oneshot
ExecStart=$HOME/.local/bin/hermes chat --toolsets skills -q "/lv-executor wykonaj zaległe zlecenia"
EOF
  cat > "$HOME/.config/systemd/user/lv-executor.timer" <<EOF
[Unit]
Description=Uruchamiaj LasVegas executor co 5 minut

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now lv-executor.timer
  say "Linux: systemd --user timer zarejestrowany."
fi

# --- 6. Pierwsza runda -------------------------------------------------------
say "Gotowe. Uruchamiam pierwszą rundę agenta…"
say "Jeśli bukmacher poprosi o logowanie — agent otworzy okno i poprosi Cię o zalogowanie RAZ."
hermes chat --toolsets skills -q "/lv-executor wykonaj zaległe zlecenia" || true

say "Instalacja zakończona. Agent pracuje w tle; status i kill switch: LasVegas → Podłącz agenta."
