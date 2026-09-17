# Playbook: Superbet (superbet.pl)

**Status: SKALIBROWANY 2026-09-17** (`updated: 2026-09-17`, `verified: yes`,
`failures: 0`). Selektory z żywej strony (Playwright + kupony agenta 17.09:
Korona – Raków i GKS – Cracovia, rożne). Ścieżkę standardową (1X2, liczba goli,
rożne, obie drużyny strzelą) przechodzi skrypt `scripts/lv-place.py` — Ty
wchodzisz, gdy skrypt odda `needs_model`, i zaczynasz od kroku, który wskazał.
Superbet jest jednym z dwóch buków sondowanych przez bramkę kolejki (obok STS):
bez świeżego (≤ 30 min) raportu `logged_in` jego zlecenia wracają z kolejki
z `loginBlocked: true` (claim = 404) — patrz Krok 0 SKILL.md.

## Zasady ogólne

- Pracuj na AX tree (`cdp("Accessibility.getFullAXTree")["nodes"]`), nie na screenshotach.
  `backendDOMNodeId` zmienia się co sesję — ZAWSZE odpytuj drzewo na nowo, nigdy nie
  zapisuj node id na później.
- W `js(...)` używaj IIFE `(() => { ... })()` — `const` w top-level koliduje między
  wywołaniami (SyntaxError: already declared).
- Koordynaty kliknięcia: `q = cdp("DOM.getBoxModel", backendNodeId=n)["model"]["content"]`,
  `x, y = sum(q[0::2])/4, sum(q[1::2])/4`, potem `click_at_xy(x, y)` (jak w `playbooks/sts.md`).

## Krok 1: wejście i cookies

1. `new_tab("https://superbet.pl")` + `wait_for_load()`.
2. Popup cookies/RODO przy pierwszym wejściu — zaakceptuj (nazwę przycisku potwierdź
   w AX tree). Banery bonusowe/boost ignoruj.

## Krok 2: weryfikacja logowania (krytyczne — Krok 0 SKILL.md)

- **Zalogowany (metoda pierwotna — stan aplikacji, skalibrowana 2026-09-13)**:
  w `js(...)` sprawdź `localStorage` strony —
  `(() => { try { return JSON.parse(localStorage.getItem("user")||"null") } catch { return null } })()`
  → obiekt z `value != null` znaczy zalogowany; dodatkowo
  `JSON.parse(localStorage.getItem("users:sessionExpire")||"null").value > Date.now()`
  potwierdza ważność sesji. `value: null` = gość.
- **Zalogowany (metoda zapasowa — DOM)**: widoczne menu konta
  (`[data-testid="account-menu"]` / `.account-menu`) — te selektory NIE są
  potwierdzone na żywej stronie; storage z metody pierwotnej wygrywa.
- **Niezalogowany**: widoczny przycisk `.e2e-login` („zaloguj") — znacznik e2e
  Superbet potwierdzony żywą sondą 2026-09-13 (nagłówek i /logowanie).
- **Saldo**: DO KALIBRACJI (`[data-testid="account-balance"]` / `.account-balance`
  to placeholdery) — jeśli nie znajdziesz kwoty, raportuj `logged_in` BEZ salda,
  nie zgaduj liczby. Znalezioną kwotę (`NNN,NN zł` → `130.50`) podaj jako
  `balanceBefore`/`balanceAfter`.
- Sesję zdobywa `python3 scripts/lv-login.py superbet`: klika `Zaloguj` w
  nagłówku (`.e2e-login`) — formularz to modal z polami
  `input[name="usernameOrEmail"]` i `input[name="password"]` oraz przyciskiem
  `#login-modal-submit` (**nie istnieje** strona `/logowanie` — 404, sprawdzone
  2026-09-13) — i czeka na `localStorage.user.value`. Przy captchy/kodzie SMS
  skrypt oddaje powód, a loguje się użytkownik w oknie agenta (nie Ty). Po
  `logged_in` ponów odczyt `localStorage` z metody pierwotnej.
- Wykryta ważna sesja → `bash scripts/lv-api.sh session superbet logged_in <saldo>`
  — świeży raport odblokowuje zlecenia buka i odświeża saldo konta w LasVegas.
- Nadal niezalogowany → `bash scripts/lv-api.sh session superbet logged_out`
  i pomiń w tym cyklu zlecenia tego buka z `loginBlocked: true` (`claim`
  odrzuciłby je 404) — zostają w kolejce na następny cykl.

## Krok 3: nawigacja do meczu

`eventUrl` ze zlecenia to strona główna z fragmentem `#event=…` — NIE prowadzi
do meczu. Szukaj: `goto_url("https://superbet.pl/wyszukaj")`, pole
`input[name="search-events"]` (placeholder „Znajdź swoją drużynę”), wpisz JEDEN
najdłuższy token nazwy gospodarza (np. `espanyol`, nie „RCD Espanyol”) i
`Enter`. Wyniki to wiersze `a.e2e-event-row` z tekstem „Jutro, 21:00 … Espanyol
Elche Mecz 1 1.85 X 3.55 2 4.15” i href
`https://superbet.pl/kursy/pilka-nozna/espanyol-vs-elche-13847086`. Wybierz
wiersz z OBIEMA drużynami i otwórz href (`goto_url`). Strona meczu ma tytuł
„Espanyol vs Elche: Kursy i Zakłady | Superbet” — sprawdź obie drużyny. Jedna
karta na bieg; po czarnym ekranie: `location.reload()`, potem `new_tab`.

## Krok 4: wybór rynku i kursu

Rynki to karty `.e2e-market` z nazwą w `.e2e-market-name`. Na starcie w DOM są
tylko karty grupy „Wszystko”; grupy przełączają chipy
`.sds-filter-bar__filter-container button` („Gole”, „Handicap”, „Połowy”,
„Rzuty rożne”, „Kartki”, „Statystyki”) — kliknij chip, dopiero potem szukaj karty.
Karta zwinięta ma przycisk `.e2e-market-collapse` (klik rozwija); „Zobacz N
więcej zakładów” to `.e2e-expand-markets`.

Kurs = `.e2e-market-odd` → wewnątrz `button.odd-button` z `aria-label`
„<rynek>, <opis>, współczynnik <kurs>, active”; nazwa kursu w `.e2e-odd-name`
(1/X/2, tak/nie), wartość w `.e2e-odd-value`. Karty tabelaryczne (Liczba goli,
rożne) NIE mają `.e2e-odd-name` — czytaj aria-label:
- `1x2` → karta „Mecz”, aria „Mecz, Espanyol wygra mecz…” (1), „Mecz, Remis w meczu…” (X),
  „Mecz, Elche wygra mecz…” (2); nazwa kursu `1`/`X`/`2` rozstrzyga;
- `ou<linia>` → chip „Gole”, karta „Liczba goli”, aria „Liczba goli, Poniżej 2.5
  goli w meczu, współczynnik 1.97” / „Powyżej 2.5 …”. Linie tylko połówkowe
  (0.5…8.5); linii całkowitej (`ou2` = 2) Superbet nie ma → `skipped line_not_found`;
- `corners_ou<linia>` → chip „Rzuty rożne”, karta „Liczba rzutów rożnych”, aria
  „…, Poniżej 11.5 …”; brak linii → `skipped line_not_found` z dostępnymi liniami;
- `btts` → karta „Obie drużyny strzelą”, kursy `tak`/`nie`.
Bramka kursu (verification-rules §1) PRZED kliknięciem: kurs z aria/`.e2e-odd-value`
niższy o >2 % niż `odds` → `skipped odds_drift`. Kupon musi być PUSTY: licznik nóg to
tekst przycisku `.clear-button` (np. „1”); pusty kupon = `.e2e-betslip-empty-placeholder`
(„Kupon jest pusty. Kliknij w kurs aby dodać mecz.”). Nogi z poprzednich biegów
usuń `.clear-button` → potwierdź „Usuń wszystko”/„Wyczyść”.

## Krok 5: stawka (KRYTYCZNE — pole wstępnie wypełnione)

Kupon: `.sds-betslip-desktop` (tryb „Prosty”), nogi w `.sds-betslip-selections`
(„Espanyol - Elche Jutro, 21:00 poniżej 2.5 Liczba goli 1.97”), pole stawki
`input[name="stake"]` (`#stake-0`, `inputmode="decimal"`). **Pole ma już kwotę**
(domyślnie 2, u użytkownika ostatnia/proponowana — 17.09: 45,22 zł!). Wpisz stawkę
przez `fill_input('input[name="stake"]', "10.68")` (Vue — zwykłe wpisanie DOPISUJE),
potem odczytaj `input.value` i sekcję „STAWKA … PLN” na kuponie. Inna kwota niż
`stake` → NIE klikaj, powtórz. „POT. WYPŁATA” = stawka × kurs × 0,88 (podatek 12 %)
— zgodność potwierdza stawkę i kurs. „KURS 1.97” na kuponie musi zgadzać się z
klikniętym kursem (bramka 2 %).

## Krok 6: postawienie i potwierdzenie

Przycisk `button.e2e-betslip-submit` („Postaw zakład”; przy wylogowaniu obok stoi
`.e2e-login` „Zaloguj” — wtedy `failed not_logged_in`). Kliknij RAZ. Potwierdzenie:
modal z tekstem **„ZAKŁAD POSTAWIONY”**, numer kuponu w formacie `8918-Y30U7K`
(4 znaki, myślnik, 6 znaków) — odczytaj z modalu; saldo w nagłówku spada o stawkę
(200,00 → 189,32 przy 10,68). Bez modalu i bez spadku salda → `failed
unknown_after_click` (weryfikacja), nigdy drugi klik. Znane komunikaty o limitach →
`bookmaker_limit`; „niewystarczające środki” → `skipped insufficient_balance`.
Raport: `bash scripts/lv-api.sh placed <betId> <numer> <kurs> <stawka> <saldoPrzed> <saldoPo>`.

## Pułapki

- Banery „bonus / boost / zgarnij" — ignoruj; nigdy nie zaznaczaj boostów (zmieniają kurs).
- Popupy cookies/RODO przy pierwszym wejściu na stronę.
- Komunikaty o limitach (minimalna/maksymalna stawka, limit czasu gry) → odpowiedni
  powód raportu; NIE zmieniaj stawki samowolnie.
- Wylogowanie w trakcie (znika menu konta, wraca `Zaloguj`) →
  `bash scripts/lv-api.sh session superbet logged_out` oraz `failed not_logged_in`
  na bieżącym zleceniu.

## Log napraw

- 2026-09-17 — kalibracja pełnej ścieżki (Playwright na żywej stronie + dwa realne
  kupony agenta: GKS – Cracovia rożne poniżej 11.5 @1.69 za 10,68; Korona – Raków
  rożne poniżej 11.5 @1.35 — ten drugi ze ZŁĄ stawką 45,22 zamiast 10, bo pole było
  wstępnie wypełnione, stąd krok 5). Ścieżka standardowa przeniesiona do
  `scripts/lv-place.py`.
- 2026-09-13 — playbook utworzony: szkielet z selektorami detekcji logowania i salda;
  placement oznaczony jako wymagający kalibracji.
- 2026-09-13 — kalibracja detekcji sesji żywą sondą Playwright: metoda pierwotna =
  `localStorage` (`user.value`, `users:sessionExpire`), wylogowany = `.e2e-login`;
  saldo nadal do kalibracji (raport `logged_in` bez salda jest poprawny).
