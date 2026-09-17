#!/usr/bin/env bash
# Instalator stacjonarnego agenta LasVegas (Hermes + skill lv-executor).
# Użycie: curl -fsSL <url>/install.sh | bash -s -- KOD_PAROWANIA
set -euo pipefail

CODE="${1:-}"
HERMES_HOME="$HOME/.hermes"
LV_API_URL_DEFAULT="https://lv.ap2ju.com"

say() { printf '\n\033[1m[lv]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[33m[lv] UWAGA:\033[0m %s\n' "$*" >&2; }
die() { printf '\n\033[1;31m[lv] BŁĄD:\033[0m %s\n' "$*" >&2; exit 1; }

say "Instalator stacjonarnego agenta LasVegas — krok po kroku wszystko zrobi za Ciebie."

# Brak podstawowego narzędzia ma skończyć się jednym zdaniem, a nie błędem powłoki
# w połowie instalacji.
command -v curl >/dev/null 2>&1 || die "Brakuje curl-a. Zainstaluj go i uruchom instalator ponownie."

# --- 0. Kod parowania -------------------------------------------------------
if [[ -z "$CODE" ]]; then
  say "Nie podano kodu parowania."
  # `curl … | bash` daje skrypt na stdin — `read` bez przekierowania zjadłby
  # KOLEJNE LINIE SKRYPTU zamiast wpisu użytkownika (prod 2026-09-07: CODE
  # kończyło się tekstem wyrażenia regularnego, curl dostawał je w URL).
  # Czytamy więc zawsze z terminala, nie z pipe'a.
  if ! read -r -p "Wklej kod z LasVegas (Podłącz agenta): " CODE < /dev/tty; then
    die "Brak terminala do wpisania kodu. Użycie: curl -fsSL <url>/install.sh | bash -s -- KOD_PAROWANIA"
  fi
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
    # To ślad, nie dowód: config po nieukończonym Quick Setupie wygląda tak samo
    # jak po udanym. Prawdziwe sprawdzenie robi pierwsza runda w kroku 6 i to ona
    # decyduje, czy instalator ma prawo zameldować sukces.
    say "Widzę ślady konfiguracji Nous Portal — nie pytam o klucz. Sprawdzę to realnie w kroku 6."
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

# --- 2. Config przeglądarki (real-profile, headed) ----------------------------
# Bez record_sessions: backend browser-use (browser_exec) nie nagrywa wideo — flaga
# obiecywałaby audyt, którego nie ma. Audytem jest transkrypt sesji Hermesa.
say "Konfiguruję przeglądarkę agenta (Twój profil, widoczne okno)…"
# python3 z modułem yaml jest na macOS pewny, na świeżym Linuksie bywa go brak.
# Pod „set -e" oznaczało to śmierć instalatora w połowie roboty, po kroku 1.
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  mkdir -p "$HERMES_HOME"
  if [[ -f "$HERMES_HOME/config.yaml" ]]; then
    warn "Brak python3 z modułem yaml — nie scalę istniejącego config.yaml. Dopisz w nim ręcznie:
    browser:
      backend: browser-use
      use_real_profile: true
      headed: true
      real_profile_autoclose: true"
  else
    cat > "$HERMES_HOME/config.yaml" <<'YAML'
browser:
  backend: browser-use
  use_real_profile: true
  headed: true
  real_profile_autoclose: true
YAML
    say "Zapisałem konfigurację przeglądarki (bez scalania — config.yaml jeszcze nie istniał)."
  fi
else
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
browser["real_profile_autoclose"] = True
cfg["browser"] = browser
os.makedirs(home, exist_ok=True)
yaml.safe_dump(cfg, open(path, "w"), sort_keys=False, allow_unicode=True)
print("OK")
PY
fi

# --- 3. Skill lv-executor z tego tapa ---------------------------------------
say "Instaluję skilla lv-executor…"
SKILL_DIR="$HERMES_HOME/skills/lv-executor"
CYCLE="$SKILL_DIR/scripts/lv-executor-cycle.sh"
SKILL_LOG="$(mktemp "${TMPDIR:-/tmp}/lv-skill-install.XXXXXX")"
hermes skills tap add PNowakBG/lasvegas-skills >/dev/null 2>&1 || true
# --force: reinstalacja ma NAPRAWDĘ podmienić skill na nowszą wersję, inaczej
# stare playbooki/scripts zostają i agent wciąż wykonuje przestarzałą procedurę.
#
# Kod wyjścia tej komendy NIE jest dowodem instalacji: 14.09 skaner skilli
# zablokował skilla (werdykt DANGEROUS), a komenda zwróciła zero — instalator
# poszedł dalej i zameldował sukces, zostawiając użytkownika bez żadnego pliku.
# Dowodem jest istnienie skryptu cyklu.
hermes skills install --force PNowakBG/lasvegas-skills/lv-executor 2>&1 | tee "$SKILL_LOG" || true
if [[ ! -f "$CYCLE" ]]; then
  if grep -qiE 'blocked|dangerous|quarantine' "$SKILL_LOG"; then
    die "Skaner skilli Hermesa zablokował instalację (werdykt wyżej) — skill NIE został zainstalowany.
    To błąd po stronie tapa, nie Twojej maszyny: zgłoś go, podając wypisane reguły.
    Pełne wyjście: $SKILL_LOG"
  fi
  die "Instalacja skilla nie zostawiła pliku: $CYCLE
    Pełne wyjście: $SKILL_LOG"
fi
rm -f "$SKILL_LOG"
say "Zainstalowana wersja skilla: $(cat "$SKILL_DIR/VERSION" 2>/dev/null || echo 'nieznana')"

# --- 4. Kod parowania → token ------------------------------------------------
say "Wymieniam kod parowania na token urządzenia…"
# Kod parowania żyje 15 minut. Do 2026-09-13 wygasły kod kończył CAŁĄ instalację
# (`die`) — a wystarczyło przepisać świeży kod z LasVegas. Teraz: maks. 3 próby,
# a po każdej porażce prosimy o nowy kod z terminala (nie z pipe'a — patrz krok 0).
EXCHANGE=""
for attempt in 1 2 3; do
  EXCHANGE=$(curl -fsS "$LV_API_URL_DEFAULT/api/executor/pairing-codes/$CODE") && break
  say "Kod '$CODE' nie zadziałał — mógł wygasnąć (15 min). Próba $attempt z 3."
  [[ "$attempt" -eq 3 ]] && die "Wymiana kodu nieudana po 3 próbach. Wygeneruj nowy kod w LasVegas (Podłącz agenta) i uruchom instalator ponownie."
  read -r -p "Wklej NOWY kod z LasVegas (Podłącz agenta): " CODE < /dev/tty \
    || die "Brak terminala do wpisania nowego kodu. Wygeneruj kod w LasVegas i uruchom: curl -fsSL <url>/install.sh | bash -s -- KOD_PAROWANIA"
  [[ "$CODE" =~ ^[A-Z0-9]{10}$ ]] || die "Kod parowania ma 10 znaków (litery/cyfry, bez 0/O/1/I). Otrzymano: '$CODE'"
done
TOKEN=$(printf '%s' "$EXCHANGE" | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
API_URL=$(printf '%s' "$EXCHANGE" | sed -n 's/.*"apiUrl"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
API_URL="${API_URL:-$LV_API_URL_DEFAULT}"
[[ -n "$TOKEN" ]] || die "Serwer nie zwrócił tokenu. Wygeneruj nowy kod w LasVegas."

# Smoke test, ZANIM instalator powie „gotowe": token zapisany ≠ token działający.
# Bez tego pierwszy cykl padał dopiero w logu launchd, a użytkownik widział
# świeżo „sparowanego" agenta, który nigdy nic nie postawił.
say "Testuję token (kill-switch)…"
curl -fsS --max-time 15 -H "Authorization: Bearer $TOKEN" \
  "$API_URL/api/executor/kill-switch" >/dev/null \
  || die "Token nie działa — kill-switch nie odpowiedział. Sparuj urządzenie ponownie w LasVegas (Podłącz agenta) i uruchom instalator jeszcze raz."

ENV_FILE="$HERMES_HOME/.env"
touch "$ENV_FILE"
# Reinstalacja: podmieniamy OBA klucze. Do 2026-09-13 przy istniejącym tokenie
# sed ruszał tylko LV_EXECUTOR_TOKEN, więc stary LV_API_URL zostawał i agent
# pukał pod nieaktualny adres.
if grep -q '^LV_EXECUTOR_TOKEN=' "$ENV_FILE"; then
  sed -i.bak "s|^LV_EXECUTOR_TOKEN=.*|LV_EXECUTOR_TOKEN=$TOKEN|" "$ENV_FILE"
  if grep -q '^LV_API_URL=' "$ENV_FILE"; then
    sed -i.bak "s|^LV_API_URL=.*|LV_API_URL=$API_URL|" "$ENV_FILE"
  else
    printf 'LV_API_URL=%s\n' "$API_URL" >> "$ENV_FILE"
  fi
else
  printf '\nLV_EXECUTOR_TOKEN=%s\nLV_API_URL=%s\n' "$TOKEN" "$API_URL" >> "$ENV_FILE"
fi
say "Token zapisany do $ENV_FILE (plik .env Hermsa — nikt go nie wkleja w czat)."

# --- 4.5. Poświadczenia bukmacherów (opcjonalnie, lokalnie) ------------------
# Pełna pętla: agent loguje się sam skryptem (scripts/lv-login.py) z pliku
# poświadczeń, który zostaje na TYM komputerze (chmod 600). Hasło nie idzie do
# LasVegas ani do modelu. Bez tego kroku agent poprosi o zalogowanie w oknie.
say "Poświadczenia bukmacherów (opcjonalnie): agent zaloguje się sam, gdy je zapiszesz."
echo "    Zostają lokalnie w $HERMES_HOME/lv-bookmakers.env — LasVegas ich nie widzi."
echo "    Pominięcie = agent poprosi Cię o zalogowanie w oknie przeglądarki."
for CRED_SLUG in sts superbet; do
  if ! read -r -p "Zapisać login i hasło do $CRED_SLUG? [t/N] " CRED_ANSWER < /dev/tty; then
    break
  fi
  case "$CRED_ANSWER" in
    t|T|tak|TAK|y|Y) bash "$CYCLE" credentials "$CRED_SLUG" < /dev/tty || warn "Poświadczenia $CRED_SLUG: próbne logowanie nie przeszło — sprawdź powyżej." ;;
    *) ;;
  esac
done

# --- 5. Autostart cyklu co 5 minut ------------------------------------------
# launchd/systemd odpala CYKL (curl-only), nie sesję Hermesa: pusta kolejka ma
# kosztować jedno żądanie HTTP, nie pełne wywołanie LLM. Historyczna lekcja:
# bezpośredni „hermes chat" co 5 min zrobił 623 sesje w 3 dni, ~620 pustych.
# Cykl budzi agenta (LLM + przeglądarka) dopiero gdy kolejka jest niepusta.
say "Rejestruję uruchamianie cyklu co 5 minut + po restarcie komputera…"
if [[ "$(uname)" == "Darwin" ]]; then
  PLIST="$HOME/Library/LaunchAgents/com.lasvegas.lv-executor.plist"
  # PATH pod launchd jest okrojony — Hermes (node) i jego toolchain muszą być
  # widoczne, inaczej cykl wołający hermesa pada na „node: command not found"
  # (hotfix produkcyjny 2026-09-08).
  LV_PATH="$HERMES_HOME/hermes-agent/venv/bin:$HERMES_HOME/hermes-agent/node_modules/.bin:$HERMES_HOME/node/bin:$HERMES_HOME/node:$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.lasvegas.lv-executor</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>$CYCLE</string>
    <string>cycle</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$LV_PATH</string>
    <key>HERMES_HOME</key><string>$HERMES_HOME</string>
  </dict>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HERMES_HOME/lv-executor.log</string>
  <key>StandardErrorPath</key><string>$HERMES_HOME/lv-executor.log</string>
</dict></plist>
EOF
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl load "$PLIST"
  say "macOS: launchd zarejestrowany (cykl curl-only, LLM tylko przy zleceniach)."
elif command -v systemctl >/dev/null 2>&1; then
  # Linux dostaje dokładnie ten sam tani cykl co macOS: curl sprawdza kolejkę,
  # a model budzi się dopiero przy niepustym wyniku. Do 14.09 timer wołał
  # „hermes chat" wprost, czyli pełną sesję LLM co 5 minut także na pustej kolejce.
  mkdir -p "$HOME/.config/systemd/user"
  LV_PATH="$HERMES_HOME/hermes-agent/venv/bin:$HERMES_HOME/node/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  cat > "$HOME/.config/systemd/user/lv-executor.service" <<EOF
[Unit]
Description=LasVegas executor (cykl lv-executor)
After=graphical-session.target

[Service]
Type=oneshot
# Bieg z dwoma zleceniami trwa 20–40 minut, a domyślny TimeoutStartSec (90 s)
# zabiłby agenta w połowie stawiania kuponu — z zleceniem zajętym w PLACING
# i kuponem w nieznanym stanie u bukmachera. Limit czasu trzyma tu lock cyklu
# i logika zleceń, nie menedżer usług.
TimeoutStartSec=infinity
Environment=PATH=$LV_PATH
Environment=HERMES_HOME=$HERMES_HOME
# Przeglądarka agenta musi przeżyć koniec cyklu. Przy domyślnym KillMode systemd
# sprząta cały cgroup zakończonej usługi, czyli zabija Chrome razem z biegiem —
# to ten sam problem, który na macOS rozwiązuje oddanie okna LaunchServices.
KillMode=process
ExecStart=/bin/bash $CYCLE cycle
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
  # Okno agenta jest widoczne (headed), więc usługa musi znać sesję graficzną.
  # Menedżer użytkownika nie dziedziczy DISPLAY sam z siebie — bez importu Chrome
  # nie ma się gdzie otworzyć, a cykl kończy się pustym CDP.
  systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY >/dev/null 2>&1 || true
  systemctl --user enable --now lv-executor.timer
  say "Linux: systemd --user timer zarejestrowany (cykl curl-only, LLM tylko przy zleceniach)."
else
  warn "Nie znalazłem systemd — autostart pomijam. Dodaj do crona wpis:
    */5 * * * * /bin/bash $CYCLE cycle"
fi

# --- 6. Pierwsza runda = jedyny prawdziwy test modelu ------------------------
say "Uruchamiam pierwszą rundę agenta…"
say "Jeśli bukmacher poprosi o logowanie — agent otworzy okno i poprosi Cię o zalogowanie RAZ."
RUN_LOG="$(mktemp "${TMPDIR:-/tmp}/lv-first-run.XXXXXX")"
hermes chat --toolsets skills -q "/lv-executor wykonaj zaległe zlecenia" 2>&1 | tee "$RUN_LOG" || true
RUN_RC=${PIPESTATUS[0]}

# Instalator nie ma prawa zameldować sukcesu, gdy pierwsza runda nie ruszyła.
# Klas porażek jest kilka i KAŻDA ma inną naprawę. 14.09 bramka łapała wyłącznie
# brak dostawcy — użytkownik dostał „Agent pracuje w tle" po HTTP 402 z OpenRoutera,
# czyli po rundzie, która nie wykonała ani jednego wywołania narzędzia.
run_problem=""
if grep -qiE 'no inference provider|run .hermes model.' "$RUN_LOG"; then
  run_problem="model"
elif grep -qiE 'http 402|more credits|credits exhausted|billing or credits' "$RUN_LOG"; then
  run_problem="kredyty"
elif grep -qiE 'http 401|invalid api key|unauthorized' "$RUN_LOG"; then
  run_problem="klucz"
elif grep -qiE 'non-retryable|traceback \(most recent' "$RUN_LOG" || [[ "$RUN_RC" -ne 0 ]]; then
  run_problem="inne"
fi

if [[ -n "$run_problem" ]]; then
  printf '\n\033[1;31m[lv] PIERWSZA RUNDA AGENTA NIE PRZESZŁA\033[0m\n' >&2
  {
    case "$run_problem" in
      model)
        echo "    Hermes nie ma skonfigurowanego dostawcy modelu — każdy cykl skończy się tak samo."
        echo "      hermes model        # Quick Setup (Nous Portal) — darmowy OAuth w przeglądarce"
        echo "      albo dopisz klucz:  echo 'OPENROUTER_API_KEY=…' >> $ENV_FILE"
        ;;
      kredyty)
        echo "    Dostawca odrzucił żądanie z powodu środków na koncie (HTTP 402)."
        echo "    OpenRouter rezerwuje koszt po max_tokens, więc przy niemal pustym saldzie"
        echo "    pada nawet krótka rozmowa — samo „mam kilka centów\" nie wystarczy."
        echo "      doładowanie:        https://openrouter.ai/settings/credits"
        echo "      albo darmowy model: hermes model"
        ;;
      klucz)
        echo "    Dostawca odrzucił poświadczenia (HTTP 401) — klucz nieważny albo nie ten."
        echo "      popraw wpis w $ENV_FILE, potem sprawdź: hermes chat -q \"powiedz ok\""
        ;;
    esac
    if [[ "$run_problem" = "inne" ]]; then
      echo "    Sesja agenta skończyła się błędem (kod $RUN_RC) — przyczyna w wyjściu powyżej."
      echo "    Pliki, parowanie i autostart są na miejscu; kolejny cykl spróbuje ponownie."
    else
      echo "    Pliki, parowanie i autostart są na miejscu — po naprawie agent ruszy sam"
      echo "    przy najbliższym cyklu (co 5 minut). Sprawdzenie: hermes chat -q \"powiedz ok\""
    fi
  } >&2
  rm -f "$RUN_LOG"
  # Porażka nierozstrzygnięta co do klasy nie musi znaczyć, że instalacja jest zła
  # (bukmacher mógł np. oddać sterowanie człowiekowi) — tam zostaje ostrzeżenie.
  [[ "$run_problem" = "inne" ]] || exit 1
else
  rm -f "$RUN_LOG"
fi

say "Instalacja zakończona. Agent pracuje w tle; status i kill switch: LasVegas → Podłącz agenta."
