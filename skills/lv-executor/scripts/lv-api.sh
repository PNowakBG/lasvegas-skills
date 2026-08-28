#!/usr/bin/env bash
# lv-api.sh — helpery API LasVegas dla agenta egzekutora (lv-executor).
# Ustawienia: LV_API_URL (default https://lv.ap2ju.com), LV_EXECUTOR_TOKEN (wymagany).
set -euo pipefail

LV_API_URL="${LV_API_URL:-https://lv.ap2ju.com}"
BASE="$LV_API_URL/api/executor"

auth_header() {
  if [[ -z "${LV_EXECUTOR_TOKEN:-}" ]]; then
    echo "BŁĄD: brak LV_EXECUTOR_TOKEN. Zainstaluj agenta komendą z LasVegas (Podłącz agenta)." >&2
    exit 2
  fi
  printf 'Authorization: Bearer %s' "$LV_EXECUTOR_TOKEN"
}

cmd="${1:-help}"
case "$cmd" in
  orders)
    curl -fsS -H "$(auth_header)" "$BASE/queue"
    ;;
  claim)
    curl -fsS -X POST -H "$(auth_header)" "$BASE/queue/$2/claim"
    ;;
  placed)
    # placed <betId> <ticketId> <actualOdds> [actualStake]
    body=$(printf '{"success":true,"ticketId":"%s","actualOdds":%s,"aborted":false}' \
      "$2" "${3:-null}")
    [[ -n "${4:-}" ]] && body=$(printf '{"success":true,"ticketId":"%s","actualOdds":%s,"actualStake":%s,"aborted":false}' "$2" "${3:-null}" "$4")
    curl -fsS -X POST -H "$(auth_header)" -H "Content-Type: application/json" \
      -d "$body" "$BASE/queue/$1/confirm"
    ;;
  failed)
    # failed <betId> <reason>
    body=$(printf '{"success":false,"aborted":false,"reason":"%s"}' "$2")
    curl -fsS -X POST -H "$(auth_header)" -H "Content-Type: application/json" \
      -d "$body" "$BASE/queue/$1/confirm"
    ;;
  skipped)
    # skipped <betId> <reason> — świadome pominięcie (odds_drift, market_mismatch…)
    body=$(printf '{"success":false,"aborted":true,"reason":"%s"}' "$2")
    curl -fsS -X POST -H "$(auth_header)" -H "Content-Type: application/json" \
      -d "$body" "$BASE/queue/$1/confirm"
    ;;
  kill-switch)
    # exit 0 = wolno stawiać; exit 1 = wstrzymane (halted albo reguła buka wyłączona)
    status=$(curl -fsS -H "$(auth_header)" "$BASE/kill-switch")
    halted=$(printf '%s' "$status" | sed -n 's/.*"halted"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p')
    if [[ "$halted" == "true" ]]; then
      echo "$status"
      exit 1
    fi
    echo "$status"
    exit 0
    ;;
  status)
    curl -fsS -H "$(auth_header)" "$BASE/kill-switch"
    ;;
  help|*)
    cat <<'EOF'
lv-api.sh — API LasVegas dla egzekutora
  orders                          lista zleceń (poll)
  claim <betId>                   podbij zlecenie (QUEUED → PLACING)
  placed <betId> <ticketId> <odds> [stake]   raport postawienia
  failed <betId> <reason>         raport porażki
  skipped <betId> <reason>        świadome pominięcie
  kill-switch                     exit 0 = wolno, exit 1 = wstrzymane
  status                          stan reguł (JSON)
Env: LV_API_URL, LV_EXECUTOR_TOKEN
EOF
    ;;
esac
