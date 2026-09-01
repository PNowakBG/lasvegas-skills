---
name: lv-executor
description: Egzekwuje zlecenia zakładów z LasVegas u bukmacherów (Superbet, STS, Betclic, Betfan)
version: 1.0.0
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [betting, automation, lasvegas]
    requires_toolsets: [terminal, browser]
    config:
      - key: lvexecutor.api_url
        description: "URL instancji LasVegas"
        default: "https://lv.ap2ju.com"
        prompt: "Adres Twojej instancji LasVegas"
---

# LV Executor — egzekucja zakładów wg rekomendacji LasVegas

## When to Use

Uruchamiaj ten skill, gdy masz wykonać zaległe zlecenia zakładów z LasVegas:
przy każdym uruchomieniu cyklu (cron/autostart po instalatorze), albo gdy
użytkownik wpisze `/lv-executor`. Kryteria typowania (edge, stawki, limity)
żyją w LasVegas w parametrach funduszu — skill ich NIE zmienia i NIE podejmuje
decyzji inwestycyjnych. Skill to tylko ręce: poll → claim → weryfikacja → postawienie → raport.

Wymaga: `LV_EXECUTOR_TOKEN` (token urządzenia z LasVegas) i włączonego
real-profile browsing w Hermesie (`browser.use_real_profile: true`).

## Procedure

Na początku załaduj `references/verification-rules.md` (twarde reguły weryfikacji)
i, przy pierwszym zleceniu danego bukmachera, `references/learning-procedure.md`
(procedura uczenia się playbooka).

Pętla egzekucji — wykonuj SEKWENCYJNIE, jedno zlecenie po drugim:

1. **Poll.** `bash scripts/lv-api.sh orders` → lista zleceń. Pusta lista: koniec,
   nic nie rób.
2. **Kill switch PRZED każdym zleceniem.** `bash scripts/lv-api.sh kill-switch` —
   gdy `halted: true` albo reguła danego bukmachera ma `enabled: false`: koniec
   biegu, nic nie stawiaj.
3. **Claim.** `bash scripts/lv-api.sh claim <betId>` — błąd 404 = ktoś inny już
   wziął zlecenie; idź do następnego.
4. **Playbook.** Załaduj `playbooks/<slug-bukmachera>.md` przez skill_view.
   Gdy nie istnieje → tryb EKSPLORUJ z `references/learning-procedure.md`
   (pierwszy kontakt z bukmacherem), potem DOKUMENTUJ playbook i kontynuuj.
5. **Weryfikacja logowania.** Otwórz `eventUrl` (deeplink z zlecenia; gdy
   `eventUrlKind: "home"` — otwórz stronę główną i WYSZUKAJ mecz wg
   `homeTeam`/`awayTeam` z playbooka). Sprawdź wg playbooka, czy jesteś
   zalogowany. Niezalogowany: otwarte okno przeglądarki jest widoczne — powiedz
   użytkownikowi w czacie: „Zaloguj się do <bukmacher> w otwartym oknie — poczekam",
   czekaj i sprawdzaj ponownie co ~30 s (max 5 min), potem przejdź dalej.
   Nadal niezalogowany → `bash scripts/lv-api.sh failed <betId> not_logged_in`
   i następne zlecenie.
6. **Budowa kuponu.** Znajdź rynek i typ zlecenia (market/outcome/selectionDetail;
   dopasowanie rozmyte: ignoruj wielkość liter, polskie znaki, „–" vs „-").
   Ustaw stawkę DOKŁADNIE `stake` (nigdy więcej, nigdy mniej, nigdy „wartość
   sugerowaną" przez bukmachera). Zignoruj banery bonusowe i boosty kursowe —
   nie klikaj niczego, co zmienia kupon.
7. **Weryfikacja kuponu na ekranie** wg `references/verification-rules.md`:
   kurs na ekranie vs `odds` (tolerancja ±2%), stawka vs `stake`, rynek/typ vs
   zlecenie, liczba selekcji w kuponie == 1 (lub liczba nóg AKO). JAKA KOLWIEK
   rozbieżność → NIE klikaj „Postaw" → `bash scripts/lv-api.sh skipped <betId> odds_drift "kurs 2.15 → 1.60"`
   (lub adekwatny powód z detail) → następne zlecenie.
8. **Postawienie.** Kliknij „Postaw"/„Zakład" zgodnie z playbookiem. Po
   potwierdzeniu odczytaj identyfikator kuponu (betId/ticket) wg playbooka.
9. **Raport.** `bash scripts/lv-api.sh placed <betId> <ticketId> <actualOdds> <actualStake> <balanceBefore> <balanceAfter>`
   — salda odczytane wg playbooka przed i po postawieniu, liczby z kropką
   (`130,50 zł` → `130.50`). Bez sald serwer nie uzgodni budżetu i nie odświeży
   salda konta w LasVegas.
   Błąd w kroku 7-8: NIE raportuj porażki od razu — najpierw tryb SAMONAPRAWY
   playbooka (`references/learning-procedure.md`); dopiero druga porażka pod rząd
   → `bash scripts/lv-api.sh failed <betId> <powód>` i powiadom użytkownika w czacie.

Twarde zakazy (obowiązują zawsze, nawet gdy zlecenie „wisi"):
- NIE stawiaj bez pozytywnego kill-switcha z kroku 2.
- NIE stawiaj stawki innej niż `stake` z zlecenia.
- NIE stawiaj, gdy kupon nie przeszedł pełnej weryfikacji z kroku 7.
- NIE loguj się hasłami z plików/URL-i — loguje się TYLKO użytkownik w otwartym oknie.
- NIE otwieraj stron poza domeną zlecenia (bukmacher) podczas egzekucji zleceń.

## Pitfalls

- Chrome 136+ blokuje zdalne debugowanie domyślnego profilu — Hermes steruje
  migawką profilu (`~/.hermes/browser-profile/`), NIE używaj `/browser connect`
  na domyślnym profilu.
- Windows: resync real-profile wymaga CAŁKOWITEJ zamkniętej przeglądarki
  (też instancja tray/background). Jeśli sesja wychodzi niezalogowana — najpierw
  to sprawdź.
- Popupy cookies/RODO przy pierwszym wejściu na buka — zaakceptuj wg playbooka.
- Limity stawek bukmachera (min/max) — gdy buk odrzuca stawkę z powodu limitu,
  to `failed: bookmaker_limit`, nie próbuj zmieniać stawki.
- 2FA/SCA przy płatnościach — nie dotyczy samych zakładów, ale wylogowanie
  po nieaktywności zdarza się często; wróć do kroku 5.
- Snapshots dużych stron bywają obcięte (`truncated: true`) — czytaj pełny
  plik z `~/.hermes/cache/web/` zamiast zgadywać ref-id.

## Verification

Po biegu:
- `bash scripts/lv-api.sh status` / LasVegas UI: zlecenia przeszły QUEUED → PLACED
  (lub FAILED z powodem).
- Audyt: pełny transkrypt sesji Hermesa (każde wywołanie narzędzia, w tym kod
  `browser_exec`) — `hermes sessions` / `hermes --resume <id>`. Nagrań wideo NIE ma:
  backend browser-use nie nagrywa mimo `browser.record_sessions`.
- Salda kont bukmacherskich widoczne w LasVegas (pętla zwrotna z potwierdzeń).
