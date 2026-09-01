# Playbook: STS (sts.pl)

Zweryfikowany E2E 2026-08-29 (Chrome, profil `~/.hermes/lv-browser-profile`, CDP :9222).

## Zasady ogólne

- Pracuj na AX tree (`cdp("Accessibility.getFullAXTree")["nodes"]`), nie na screenshotach.
  `backendDOMNodeId` zmienia się co sesję — ZAWSZE odpytuj drzewo na nowo, nigdy nie
  zapisuj node id na później.
- W `js(...)` używaj IIFE `(() => { ... })()` — `const` w top-level koliduje między
  wywołaniami (SyntaxError: already declared).
- Koordynaty kliknięcia: `q = cdp("DOM.getBoxModel", backendNodeId=n)["model"]["content"]`,
  `x, y = sum(q[0::2])/4, sum(q[1::2])/4`, potem `click_at_xy(x, y)`.

## Krok 1: wejście i cookies

1. `new_tab("https://www.sts.pl")` + `wait_for_load()`.
2. Jeśli popup cookies (dialog „KORZYSTAMY Z PLIKÓW COOKIES"): znajdź w AX tree
   `button` o nazwie dokładnie `Akceptuj wszystkie` i kliknij. Popup znika.

## Krok 2: weryfikacja logowania

- Zalogowany: w AX tree (role button/link/StaticText) widać `Wpłata` oraz `Depozyt NNN,NN zł`.
- Niezalogowany: widać `Zaloguj się` i `Załóż konto`. Wtedy procedura logowania ze SKILL.md
  (poproś użytkownika, czekaj ~30 s × max 5 min; dalej `failed not_logged_in`).
- Odczytaj saldo z tekstu `Depozyt NNN,NN zł` (prawy górny róg) → `balanceBefore`.

## Krok 3: nawigacja do meczu

**Zlecenie niesie `eventUrl`.** Gdy `eventUrlKind` = `event`, otwórz go BEZPOŚREDNIO
(`goto_url(eventUrl)` w bieżącej karcie) — żadnego szukania. Dopiero gdy `eventUrl`
jest null/`home`, szukaj: karta meczu na stronie głównej albo lupa → nazwa drużyny →
wynik meczu. URL meczu ma postać `/kursy/<slug>/<id>` — zweryfikuj, że tytuł strony
zawiera OBIE drużyny.

**Dyscyplina kart:** pracuj w JEDNEJ karcie (`goto_url`), nie otwieraj nowych
(`new_tab` tylko na samym początku, gdy nie ma żadnej prawdziwej karty). Strona meczu
STS jest ciężka (live-kursy + trackery) — każda dodatkowa karta mnoży CPU i potrafi
zawiesić `Runtime.evaluate`.

**Renderer zawieszony (eval timeout):** gdy `js(...)`/`page_info()` wisi do timeoutu —
karta jest martwa. Zamknij ją przez CDP HTTP: `curl http://localhost:9222/json`,
znajdź `id` karty sts.pl, `curl http://localhost:9222/json/close/<id>`, potem
`new_tab(eventUrl)` i pracuj dalej. Nigdy nie zostawiaj duplikatów kart meczu.

## Krok 4: wybór rynku i kursu

1. Rynek `1x2` ma nagłówek `Mecz` (StaticText). Pod nim trzy przyciski kursów:
   `1 <kurs>` (home), `X <kurs>` (remis), `2 <kurs>` (away) — np. `1 2.95`.
2. Wybierz przycisk wg `outcome` ze zlecenia (home→`1 `, draw→`X `, away→`2 `;
   dokładniej: prefiks przed spacją musi pasować do wyniku rynku 1x2).
3. PRZED kliknięciem przeczytaj kurs z etykiety przycisku (spacja, potem liczba z
   kropką, np. `1 2.95` → 2.95). Jeśli < `minOdds` ze zlecenia → NIE klikaj; raport
   `bash scripts/lv-api.sh skipped <betId> odds_drift "kurs 2.10 → 1.95"` (podaj aktualny kurs w detail). Kurs równy `odds` ze zlecenia
   lub minimalnie niższy, ale >= `minOdds` → klikaj (to normalna zmiana, nie drift).
4. Kliknij — po prawej pojawia się kupon z selekcją. Po kliknięciu przeczytaj kurs
   Z KUPONU i porównaj z `minOdds`; dopiero gdy kupon pokazuje mniej → usuń selekcję
   (X na kuponie) i `bash scripts/lv-api.sh skipped <betId> odds_drift "kurs 2.10 → 1.95"`.

### Rynek rożnych (`corners_ouNNN`) — od 29.08

Zlecenie niesie rynek kanoniczny `corners_ou<cyfry>`: dekoduj linię jako
cyfry/10 (`corners_ou105` → 10,5; `corners_ou95` → 9,5). Strona:
`corners_under` → przycisk „Poniżej <linia>", `corners_over` → „Powyżej <linia>".

1. Na stronie meczu znajdź w AX tree sekcję rożnych — nagłówek zawiera
   „Rzuty rożne" / „Rożne". Sekcja ma WIELE linii (8,5 / 9,5 / 10,5 / 11,5…),
   każda z parą przycisków „Powyżej X" / „Poniżej X" (kurs w etykiecie).
2. Wybierz przycisk DOKŁADNIE z linią ze zlecenia. Jeśli linii nie ma w ofercie
   → `bash scripts/lv-api.sh skipped <betId> line_not_found "dostępne: 8,5 / 9,5 / 11,5"` (podaj dostępne linie w detail) — NIGDY nie klikaj
   „najbliższej" linii, to inny zakład.
3. Bramka kursu i weryfikacja kuponu: identycznie jak w 1x2 (punkty 3-4 wyżej).
   Na kuponie selekcja musi czytać się jako rożne z właściwą linią — jeśli kupon
   pokazuje inną linię niż zlecenie → usuń selekcję i `failed wrong_line`.

Status: sekcja dodana przed pierwszym żywym zleceniem rożnych — przy nim
zweryfikuj dokładne nazwy etykiet i DOPRECYZUJ ten przepis (learning-procedure).

## Krok 5: stawka (KRYTYCZNE — Angular)

Input stawki: `input[inputmode="decimal"]` (jedyny widoczny input tekstowy na stronie;
domyślnie pokazuje ulubioną stawkę, np. `5,00`). Zwykłe fill DOPISUJE zamiast nadpisać.

Dokładna sekwencja (sprawdzona):

```python
js("""(() => {
  const inp = document.querySelector('input[inputmode="decimal"]');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(inp, '');
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.focus();
})()""")
type_text("<stawka>")          # np. "1" — tylko cyfry, bez przecinka
js("""(() => {
  const inp = document.querySelector('input[inputmode="decimal"]');
  inp.dispatchEvent(new Event('change', {bubbles: true}));
  inp.blur();
})()""")
```

Weryfikacja: przycisk w AX tree MUSI pokazywać `Postaw <stawka>,00 zł` (np. stawka 1 →
`Postaw 1,00 zł`). Jeśli pokazuje inną kwotę — powtórz sekwencję; nigdy nie klikaj Postaw
przy złej kwocie.

## Krok 6: postawienie i potwierdzenie

1. Kliknij przycisk `Postaw ... zł`.
2. Jeśli wyskoczy dialog zmiany kursu — zaakceptuj TYLKO gdy nowy kurs >= `minOdds`;
   inaczej anuluj i `skipped odds_drift`.
3. Poczekaj ~3 s. Ekran potwierdzenia pokazuje „Przyjęliśmy Twój kupon!", „Kurs
   całkowity" i „Możesz wygrać". Uwaga na modal „Dzień Bonuserii" (1/14) — zamknij go (X).
4. **ticketId — najpewniejsze źródło to sieć, nie UI.** Przed kliknięciem Postaw włącz
   `cdp("Network.enable")`; po kliknięciu `drain_events()` i znajdź odpowiedź POST-a
   stawiającego kupon (url zawiera bet/coupon/ticket) — body odpowiedzi
   (`cdp("Network.getResponseBody", requestId=...)`) zawiera numer kuponu.
   Fallback: `Moje kupony` → `W grze` → najnowszy kupon → detal/numer.
   Ostateczność (gdy oba zawiodą): ticketId = betId — lepsze to niż brak raportu.
5. Odczytaj ponownie `Depozyt NNN,NN zł` → `balanceAfter`. Saldo powinno spaść dokładnie
   o stawkę (STS: z konta schodzi stawka brutto; „Możesz wygrać" liczone od stawki netto
   po podatku 12% — np. 2 zł → netto 1,76 zł → wygrana 5,19 zł przy kursie 2.95).
6. Raport: `bash scripts/lv-api.sh placed <betId> <ticketId> <actualOdds> <actualStake> <balanceBefore> <balanceAfter>`
   (actualOdds = kurs z kuponu, actualStake = kwota z przycisku Postaw, salda z kroku 2
   i punktu 5 jako liczby z kropką: `Depozyt 130,50 zł` → `130.50`). Serwer porównuje
   spadek salda ze stawką i przy rozjeździe wyłącza regułę auto-place — to bezpiecznik,
   nie statystyka; bez sald go nie ma.

## Pułapki

- Banery „bonus / boost / zgarnij" — ignoruj, nigdy nie zaznaczaj boostów (zmieniają kurs).
- **Minimalna stawka STS: 2 zł.** Zlecenie ze stawką < 2 zł odbije się od kasy —
  raport `skipped bookmaker_limit` (nie próbuj podnosić stawki samowolnie).
- Przycisk `Postaw` nieaktywny / toast z błędem (np. „minimalna stawka") → screenshot +
  `failed ui_error` z treścią błędu.
- Wylogowanie w trakcie (znów widać `Zaloguj się`) → `failed not_logged_in`.
- Nie klikaj `Postaw` dwa razy — po kliknięciu czekaj na ekran potwierdzenia.
