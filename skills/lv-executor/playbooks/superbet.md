# Playbook: Superbet (superbet.pl)

**Status: NIECALIBROWANY (`CALIBRATED: nie`)** — `updated: 2026-09-13`, `verified: no`,
`failures: 0`. Detekcja logowania i salda ma selektory z DOM Superbet; placement
(rynek → kupon → stawka → „Postaw") wymaga kalibracji trybem EKSPLORUJ wg
`references/learning-procedure.md`. Zlecenia bez `verified: yes` NIE idą realnie.
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
- Sesję zdobywa TYLKO użytkownik: otwórz `https://superbet.pl/` i kliknij
  `Zaloguj` w nagłówku (`.e2e-login`) — formularz to modal; **nie istnieje**
  strona `/logowanie` (404, sprawdzone 2026-09-13). Poproś go
  w czacie i czekaj (sprawdzaj co ~30 s, max 5 min); po jego logowaniu ponów
  odczyt `localStorage` z metody pierwotnej.
- Wykryta ważna sesja → `bash scripts/lv-api.sh session superbet logged_in <saldo>`
  — świeży raport odblokowuje zlecenia buka i odświeża saldo konta w LasVegas.
- Nadal niezalogowany → `bash scripts/lv-api.sh session superbet logged_out`
  i pomiń w tym cyklu zlecenia tego buka z `loginBlocked: true` (`claim`
  odrzuciłby je 404) — zostają w kolejce na następny cykl.

## Krok 3: nawigacja do meczu

DO KALIBRACJI. Gdy `eventUrlKind` = `event` — otwórz `eventUrl` BEZPOŚREDNIO
(`goto_url` w istniejącej karcie). Gdy `eventUrl` jest null/`home` — szukaj meczu po
`homeTeam`/`awayTeam` (lupa albo nawigacja po lidze) i zweryfikuj, że tytuł strony
zawiera OBIE drużyny. Pracuj w JEDNEJ karcie na bieg (dodatkowe karty mnożą CPU).

## Krok 4: wybór rynku i kursu

DO KALIBRACJI (tryb EKSPLORUJ) — ścieżka `market`/`outcome` → etykiety Superbet do
ustalenia na żywym ekranie. Bramka kursu z `references/verification-rules.md` §1
obowiązuje NIEZALEŻNIE od stanu kalibracji. Kupon musi być PUSTY przed pierwszym
klikiem — usuń nogi z poprzednich biegów.

## Krok 5: stawka

DO KALIBRACJI. Mechanika wejścia stawki zależy od frameworku strony; potwierdź ją na
ekranie i sprawdź, że przycisk stawiania pokazuje DOKŁADNIE `stake` ze zlecenia
(wzorzec sekwencji: `playbooks/sts.md`, krok 5).

## Krok 6: postawienie i potwierdzenie

DO KALIBRACJI. Dopóki łańcuch nie jest zweryfikowany, NIE klikaj „Postaw" —
najpierw przebieg próbny do ekranu kuponu (learning-procedure → WERYFIKUJ). Po
kalibracji opisz: dokładną etykietę przycisku stawiającego, ekran potwierdzenia
i źródło `ticketId` (najpewniej odpowiedź POST-a przez `cdp("Network.enable")` +
`drain_events()`).

## Pułapki

- Banery „bonus / boost / zgarnij" — ignoruj; nigdy nie zaznaczaj boostów (zmieniają kurs).
- Popupy cookies/RODO przy pierwszym wejściu na stronę.
- Komunikaty o limitach (minimalna/maksymalna stawka, limit czasu gry) → odpowiedni
  powód raportu; NIE zmieniaj stawki samowolnie.
- Wylogowanie w trakcie (znika menu konta, wraca `Zaloguj`) →
  `bash scripts/lv-api.sh session superbet logged_out` oraz `failed not_logged_in`
  na bieżącym zleceniu.

## Log napraw

- 2026-09-13 — playbook utworzony: szkielet z selektorami detekcji logowania i salda;
  placement oznaczony jako wymagający kalibracji.
- 2026-09-13 — kalibracja detekcji sesji żywą sondą Playwright: metoda pierwotna =
  `localStorage` (`user.value`, `users:sessionExpire`), wylogowany = `.e2e-login`;
  saldo nadal do kalibracji (raport `logged_in` bez salda jest poprawny).
