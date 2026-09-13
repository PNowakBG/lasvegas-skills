#!/usr/bin/env bash
# lv-api.sh — helpery API LasVegas dla agenta egzekutora (lv-executor).
# Ustawienia: LV_API_URL (default https://lv.ap2ju.com), LV_EXECUTOR_TOKEN (wymagany).
set -euo pipefail

# Token i URL: najpierw środowisko, a gdy tokenu nie ma — ~/.hermes/.env (ten sam
# odczyt co load_env w lv-executor-cycle.sh). Dzięki temu wskazówka drukowana
# przez `lv-executor-cycle.sh login` („Zgłoś zalogowanie: … session …") jest
# wykonywalna od razu, bez eksportowania zmiennych w powłoce użytkownika.
LV_ENV_FILE="${HERMES_HOME:-$HOME/.hermes}/.env"
read_env_key() {
  [ -f "$LV_ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$LV_ENV_FILE" 2>/dev/null | tail -1 | tr -d '"'"'"' ' || true
}
[[ -n "${LV_EXECUTOR_TOKEN:-}" ]] || LV_EXECUTOR_TOKEN="$(read_env_key LV_EXECUTOR_TOKEN)"
[[ -n "${LV_API_URL:-}" ]] || LV_API_URL="$(read_env_key LV_API_URL)"
LV_API_URL="${LV_API_URL:-https://lv.ap2ju.com}"
BASE="$LV_API_URL/api/executor"

# Limit czasu na pojedyncze żądanie. Domyślnie brak (tak jak dotąd — nie zmieniamy
# zachowania istniejącym wywołaniom). Cykl agenta ustawia LV_API_TIMEOUT=15:
# bez limitu zawieszone połączenie trzyma lock i blokuje kolejne przebiegi.
#
# Brak tokenu to TWARDY błąd wykrywany na STARCIE komendy (`require_token` przy
# `case` niżej). Sprawdzenie NIE może stać w `auth_header`: to wywołanie stoi
# w `$(…)`, więc `exit 2` kończyłoby tylko podpowłokę, komunikat leciał na
# stderr, a curl poszedłby BEZ nagłówka (401 zamiast czytelnej instrukcji).
require_token() {
  if [[ -z "${LV_EXECUTOR_TOKEN:-}" ]]; then
    echo "BŁĄD: brak LV_EXECUTOR_TOKEN. Zainstaluj agenta komendą z LasVegas (Podłącz agenta)." >&2
    exit 2
  fi
}

# Nagłówek buduje się dopiero po sprawdzeniu tokenu (`require_token`).
auth_header() {
  printf 'Authorization: Bearer %s' "$LV_EXECUTOR_TOKEN"
}

# JEDYNE miejsce w tym skillu, które rozmawia z API LasVegas i jedyne, które
# dotyka tokenu urządzenia. Nagłówek powstaje w `auth_header`, więc token nigdy
# nie stoi w linii polecenia curl — dzięki temu da się go prześwietlić w jednym
# miejscu zamiast szukać po całym skillu. Nowe wywołania API dopisuj TUTAJ.
api_curl() {
  if [[ -n "${LV_API_TIMEOUT:-}" ]]; then
    curl -fsS --max-time "$LV_API_TIMEOUT" "$@"
  else
    curl -fsS "$@"
  fi
}

# KAŻDA gałąź nazywa swoje argumenty jawnie (betId=$2, …) zamiast wstawiać
# `$1`/`$2` prosto do URL-a i ciała żądania.
#
# Ten skrypt zawiódł dokładnie na liczeniu pozycji: `$1` to NAZWA KOMENDY, a
# mimo to trafiało do adresu jako identyfikator zlecenia. Agent wołał
# `POST /queue/skipped/confirm` i `POST /queue/placed/confirm` zamiast
# `POST /queue/<betId>/confirm`; serwer dostawał status w miejscu UUID-a,
# odpowiadał błędem, potwierdzenie nigdy nie dochodziło, zlecenie wracało do
# kolejki i przy każdym podejściu otwierało nowe okno przeglądarki.
# Produkcja 31.08: 28 takich wywołań w 3 godziny, ani jednego poprawnego.
#
# Nazwane zmienne są tu warte swojej długości: przy pieniądzach „która to była
# pozycja" nie może być pytaniem, na które trzeba odpowiadać z pamięci.
cmd="${1:-help}"

# Każda komenda poza pomocą wymaga tokenu. To sprawdzenie stoi na poziomie
# WYKONANIA komendy, a nie w `auth_header` — powód w komentarzu przy nim.
case "$cmd" in
  orders|claim|placed|failed|skipped|kill-switch|verifications|verify|status|agent-config|session)
    require_token
    ;;
esac

case "$cmd" in
  orders)
    api_curl -H "$(auth_header)" "$BASE/queue"
    ;;
  claim)
    # claim <betId>
    betId="$2"
    api_curl -X POST -H "$(auth_header)" "$BASE/queue/$betId/claim"
    ;;
  placed)
    # placed <betId> <ticketId> <actualOdds> [actualStake] [balanceBefore] [balanceAfter]
    #
    # Salda są opcjonalne składniowo, ale gdy playbook je odczytał — PODAJ OBA.
    # Serwer porównuje „ile wg naszych ksiąg miało ubyć" z „ile realnie ubyło"
    # (reconcileBalances) i przy rozjeździe WYŁĄCZA regułę auto-place. Bez sald
    # ten bezpiecznik dla toru agenta nie istnieje, a lustro salda konta w
    # LasVegas nigdy się nie odświeża. Liczby z kropką: `Depozyt 130,50 zł` → 130.50.
    betId="$2"
    ticketId="$3"
    actualOdds="${4:-null}"
    actualStake="${5:-}"
    balanceBefore="${6:-}"
    balanceAfter="${7:-}"
    body=$(printf '{"success":true,"ticketId":"%s","actualOdds":%s,"aborted":false' \
      "$ticketId" "$actualOdds")
    [[ -n "$actualStake" ]] && body="$body,\"actualStake\":$actualStake"
    [[ -n "$balanceBefore" ]] && body="$body,\"balanceBefore\":$balanceBefore"
    [[ -n "$balanceAfter" ]] && body="$body,\"balanceAfter\":$balanceAfter"
    body="$body}"
    api_curl -X POST -H "$(auth_header)" -H "Content-Type: application/json" \
      -d "$body" "$BASE/queue/$betId/confirm"
    ;;
  failed)
    # failed <betId> <reason> [detail]
    # Detail jest tak samo ważny jak przy `skipped`: 01.09 agent zgłosił
    # `bookmaker_limit "Dzienny limit czasu gry osiągnięty"`, a bez tego pola
    # w LasVegas został STARY detail z poprzedniej porażki (o dwóch nogach kuponu).
    betId="$2"
    reason="$3"
    body=$(printf '{"success":false,"aborted":false,"reason":"%s"}' "$reason")
    if [[ -n "${4:-}" ]]; then
      detail_escaped=$(printf '%s' "$4" | sed 's/\\/\\\\/g; s/"/\\"/g')
      body=$(printf '{"success":false,"aborted":false,"reason":"%s","reasonDetail":"%s"}' "$reason" "$detail_escaped")
    fi
    api_curl -X POST -H "$(auth_header)" -H "Content-Type: application/json" \
      -d "$body" "$BASE/queue/$betId/confirm"
    ;;
  skipped)
    # skipped <betId> <reason> [detail] — świadome pominięcie (odds_drift, market_mismatch…)
    betId="$2"
    reason="$3"
    body=$(printf '{"success":false,"aborted":true,"reason":"%s"}' "$reason")
    if [[ -n "${4:-}" ]]; then
      detail_escaped=$(printf '%s' "$4" | sed 's/\\/\\\\/g; s/"/\\"/g')
      body=$(printf '{"success":false,"aborted":true,"reason":"%s","reasonDetail":"%s"}' "$reason" "$detail_escaped")
    fi
    api_curl -X POST -H "$(auth_header)" -H "Content-Type: application/json" \
      -d "$body" "$BASE/queue/$betId/confirm"
    ;;
  kill-switch)
    # exit 0 = wolno stawiać; exit 1 = wstrzymane (halted albo reguła buka wyłączona)
    status=$(api_curl -H "$(auth_header)" "$BASE/kill-switch")
    halted=$(printf '%s' "$status" | sed -n 's/.*"halted"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p')
    if [[ "$halted" == "true" ]]; then
      echo "$status"
      exit 1
    fi
    echo "$status"
    exit 0
    ;;
  verifications)
    # Lista zleceń do WERYFIKACJI: próba padła bez potwierdzenia, więc kupon
    # mógł wejść u buka. Rozstrzygnij po otwartych kuponach / saldzie, potem `verify`.
    api_curl -H "$(auth_header)" "$BASE/queue/verify"
    ;;
  verify)
    # verify <betId> <placed:true|false> [ticketId] [detail]
    # placed=true  → kupon JEST na koncie (znalazłeś go w otwartych zakładach);
    # placed=false → kuponu NIE MA (saldo bez zmian) → zlecenie wróci do kolejki.
    betId="$2"
    placed="$3"
    body=$(printf '{"placed":%s' "$placed")
    [[ -n "${4:-}" ]] && body="$body,\"ticketId\":\"$4\""
    if [[ -n "${5:-}" ]]; then
      detail_escaped=$(printf '%s' "$5" | sed 's/\\/\\\\/g; s/"/\\"/g')
      body="$body,\"reasonDetail\":\"$detail_escaped\""
    fi
    body="$body}"
    api_curl -X POST -H "$(auth_header)" -H "Content-Type: application/json" \
      -d "$body" "$BASE/queue/$betId/verify"
    ;;
  status)
    api_curl -H "$(auth_header)" "$BASE/kill-switch"
    ;;
  agent-config)
    # Model, na którym ma pracować agent egzekutora (provider + model).
    # Serwer jest jedynym źródłem prawdy: zmiana modelu — także wycofanie go
    # przez dostawcę — nie wymaga dotykania skryptów na maszynie.
    api_curl -H "$(auth_header)" "$BASE/agent-config"
    ;;
  session)
    # session <bookmaker> <logged_in|logged_out> [balance]
    #
    # Meldunek stanu logowania u bukmachera (Krok 0 procedury): LasVegas wie,
    # że urządzenie ma świeżą sesję u tego buka, ZANIM pójdzie zlecenie.
    # Bramka kolejki czyta ten raport: `logged_in` starzeje się po 30 min,
    # `logged_out` flaguje zlecenia buka (`loginBlocked: true`, claim = 404)
    # do następnego raportu. Sondowani
    # bukmacherzy: superbet, sts — pozostali przechodzą bez raportu.
    # Saldo opcjonalne, liczba z kropką: `130,50 zł` → 130.50.
    bookmaker="${2:-}"
    loginState="${3:-}"
    [[ -n "$bookmaker" ]] || { echo "BŁĄD: session wymaga sluga bukmachera (np. sts, superbet)." >&2; exit 2; }
    case "$loginState" in
      logged_in) loggedIn=true ;;
      logged_out) loggedIn=false ;;
      *) echo "BŁĄD: stan logowania to logged_in albo logged_out (otrzymano: '${loginState}')." >&2; exit 2 ;;
    esac
    body=$(printf '{"bookmaker":"%s","loggedIn":%s' "$bookmaker" "$loggedIn")
    [[ -n "${4:-}" ]] && body="$body,\"balance\":$4"
    body="$body}"
    api_curl -X POST -H "$(auth_header)" -H "Content-Type: application/json" \
      -d "$body" "$BASE/session"
    ;;
  help|*)
    cat <<'EOF'
lv-api.sh — API LasVegas dla egzekutora
  orders                          lista zleceń (poll)
  agent-config                    model agenta z LasVegas (provider + model) — pyta o to cykl
  session <bookmaker> <logged_in|logged_out> [balance]   stan logowania u buka (Krok 0; bramka: superbet, sts)
  verifications                   lista zleceń do weryfikacji (kupon mógł wejść bez potwierdzenia)
  verify <betId> <true|false> [ticketId] [detail]   rozstrzyga weryfikację (true = kupon na koncie)
  claim <betId>                   podbij zlecenie (QUEUED → PLACING)
  placed <betId> <ticketId> <odds> [stake] [balanceBefore] [balanceAfter]   raport postawienia (salda = bezpiecznik budżetu)
  failed <betId> <reason> [detail]   raport porażki (detail: co dokładnie powiedział bukmacher)
  skipped <betId> <reason> [detail]  świadome pominięcie (detail wymagany)
  kill-switch                     exit 0 = wolno, exit 1 = wstrzymane
  status                          stan reguł (JSON)
Env: LV_API_URL, LV_EXECUTOR_TOKEN
EOF
    ;;
esac
