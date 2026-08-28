# Twarde reguły weryfikacji kuponu (niezmienne, nieuczalne)

Te reguły NIE podlegają playbookom ani samouczeniu. Agent ma je wykonać ZAWSZE,
przed każdym kliknięciem „Postaw". Naruszenie którejkolwiek = brak postawienia.

## 1. Kurs

- Odczytaj kurs z ekranu kuponu (nie z oferty — liczy się ten na kuponie).
- Tolerancja: `|odds_ekran − odds_zlecenie| / odds_zlecenie ≤ 0.02`.
- Kurs HIGHER niż w zleceniu jest OK (bonus dla nas) — ale tylko w tolerancji.
  Wyraźnie wyższy kurs (np. +5%) = podejrzany rynek; potraktuj jak `odds_drift`.
- Kurs niższy o więcej niż 2% → `skipped: odds_drift`. Nie negocjuj, nie poprawiaj.

## 2. Stawka

- Stawka na ekranie == `stake` z zlecenia (co do grosza).
- Zabronione: zaokrąglanie „na korzyść", „sugestie" buka, dodanie drugiego zakładu,
  system/AKO gdy zlecenie jest pojedyncze (poza jawnymi nogami AKO w zleceniu).
- Saldo konta: gdy da się je odczytać i jest < `stake` → `failed: insufficient_balance`.

## 3. Rynek i typ

- Dopasowanie rozmyte nazw: lowercase, bez polskich znaków (ą→a, ł→l…),
- „–"/„-"/"vs" równoważne, kolejność drużyn wg zlecenia (home/away).
- `selectionDetail` (np. „2-1" dla correct score) ma pierwszeństwo przed samym kodem `outcome`.
- Niepewność co do rynek/typ → NIE stawiaj → `skipped: market_mismatch`.

## 4. Kupon

- Liczba selekcji: 1 dla zwykłego zlecenia; dokładnie nogi AKO dla zleceń AKO.
- Zero dodatków: brak boostów, brak „+10% wygranej", brak opcji cashout wymuszonych.
- Ekran musi być ekranem KUPONU, nie oferty — potwierdź po elementach z playbooka.

## 5. Stan egzekucji

- Kill switch sprawdzony PRZED tym zleceniem (odpowiedź musi być świeża, nie z cache).
- Claim na zleceniu wykonany (PLACING) — nie stawiaj nie-claimniętego zlecenia.
- Nagrywanie sesji działa (record_sessions w Hermesie) — to jedyny audyt realnych pieniędzy.

## Powody raportowane do LasVegas (kody)

| Kod | Znaczenie |
|-----|-----------|
| `odds_drift` | kurs poza tolerancją |
| `market_mismatch` | rynek/typ nie do potwierdzenia na ekranie |
| `insufficient_balance` | saldo < stawka |
| `not_logged_in` | logowanie nieudane mimo prośby do usera |
| `bookmaker_limit` | buk odrzucił stawkę (limit min/max) |
| `ui_error` | błąd DOM/UI po samonaprawie playbooka |
