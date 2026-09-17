# Instalator stacjonarnego agenta LasVegas (Hermes + skill lv-executor) — Windows.
# Użycie: irm https://raw.githubusercontent.com/PNowakBG/lasvegas-skills/main/install.ps1 | iex
# Kod parowania wpisujesz raz, w kroku 4 — instalator sam o niego zapyta.

$ErrorActionPreference = "Stop"
$hermesHome = Join-Path $env:USERPROFILE ".hermes"
$lvApiUrlDefault = "https://lv.ap2ju.com"
# Nie `param()`: `irm … | iex` nie przekazuje argumentów do skryptu, więc kod
# parowania bierzemy z tej zmiennej, a gdy pusta — pytamy w kroku 4 (jak install.sh).
$PairingCode = ""

function Say($msg) { Write-Host "`n[lv] $msg" -ForegroundColor Cyan }

function Die($msg) {
  Write-Host "`n[lv] BŁĄD: $msg" -ForegroundColor Red
  # Uruchomiony jako plik (powershell -File) kończy się kodem 1. Pod `irm | iex`
  # `exit` zamknąłby okno użytkownika razem z komunikatem — zostawiamy wtedy
  # błąd terminujący, żeby dało się go przeczytać.
  if ($PSScriptRoot) { exit 1 }
  throw $msg
}

Say "Instalator stacjonarnego agenta LasVegas — wszystko zrobi za Ciebie."

# --- 1. Hermes Agent ---------------------------------------------------------
$hermesCmd = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $hermesCmd) {
  Say "Instaluję Hermes Agent (oficjalny installer, może potrwać kilka minut)…"
  Write-Host ""
  Say "UWAGA — w trakcie instalacji Hermes zapyta: 'How would you like to set up Hermes?'"
  Write-Host "   -> Wybierz:  Quick Setup (Nous Portal)   [pierwsza opcja]" -ForegroundColor Green
  Write-Host "      (darmowy login OAuth przez przegladarke, zero kluczy API, model i"
  Write-Host "       narzedzia wlaczone automatycznie — dokladnie tego potrzebuje agent)"
  Write-Host ""
  Write-Host "   -> Na KAZDYM kolejnym ekranie wyboru wybieraj opcje LOKALNE:" -ForegroundColor Green
  Write-Host "      terminal backend -> Local, przegladarka -> local Chrome/ten komputer."
  Write-Host "      Docker/cloud/sandbox/remote pozbawia agenta Twojego profilu przegladarki."
  Write-Host ""
  Start-Sleep -Seconds 2
  Invoke-RestMethod https://hermes-agent.nousresearch.com/install.ps1 | Invoke-Expression
  $env:PATH = "$env:PATH;$env:USERPROFILE\.local\bin"
  $hermesCmd = Get-Command hermes -ErrorAction SilentlyContinue
}
if (-not $hermesCmd) { throw "Hermes nie jest dostępny w PATH. Otwórz nowy terminal i uruchom instalator ponownie." }
Say "Hermes Agent: OK"

# --- 2. Config przeglądarki (real-profile, headed) ----------------------------
# Bez record_sessions: backend browser-use nie nagrywa wideo; audyt = transkrypt sesji Hermesa.
Say "Konfiguruję przeglądarkę agenta (Twój profil, widoczne okno)…"
New-Item -ItemType Directory -Force -Path $hermesHome | Out-Null
$configPath = Join-Path $hermesHome "config.yaml"
$config = @{}
if (Test-Path $configPath) {
  try {
    # Prosty parser: szukamy sekcji browser: (instalator hermsa też ją zapisuje)
    $configText = Get-Content $configPath -Raw
  } catch { $configText = "" }
} else { $configText = "" }

$browserBlock = @"
browser:
  backend: "browser-use"
  use_real_profile: true
  headed: true
  real_profile_autoclose: true
"@

if ($configText -match "(?m)^browser:\r?$" -and $configText -match "use_real_profile") {
  Say "Sekcja browser już skonfigurowana — pomijam."
} elseif ($configText -match "(?m)^browser:\r?$") {
  Say "Sekcja browser istnieje, ale bez real-profile — dopisuję klucze."
  $patched = $configText -replace "(?m)^browser:\r?$", ($browserBlock -replace '^', '' -split "`r?`n" -join "`n")
  Set-Content -Path $configPath -Value $patched -Encoding UTF8
} else {
  Set-Content -Path $configPath -Value $browserBlock -Encoding UTF8
}

# --- 3. Skill lv-executor z tapa ---------------------------------------------
Say "Instaluję skilla lv-executor…"
hermes skills tap add PNowakBG/lasvegas-skills 2>$null | Out-Null
hermes skills install PNowakBG/lasvegas-skills/lv-executor

# --- 4. Kod parowania → token → .env Hermesa ---------------------------------
# Kod parowania żyje 15 minut, więc do 3 prób z ponownym pytaniem (wzorzec z
# install.sh) — a token testujemy kill-switchem ZANIM powiemy „gotowe". Do
# 2026-09-13 krok obiecywał tylko, że Hermes zapyta w czacie: nie było ani
# wymiany kodu, ani zapisu .env, więc KAŻDY cykl agenta na Windows padał na
# braku LV_EXECUTOR_TOKEN.
if (-not $PairingCode) {
  Say "Nie podano kodu parowania."
  $PairingCode = Read-Host "Wklej kod parowania z LasVegas (Podłącz agenta)"
}
if ($PairingCode -notmatch '^[A-Z0-9]{10}$') {
  Die "Kod parowania ma 10 znaków (litery/cyfry, bez 0/O/1/I). Otrzymano: '$PairingCode'"
}

Say "Wymieniam kod parowania na token urządzenia…"
$exchange = $null
for ($attempt = 1; $attempt -le 3; $attempt++) {
  try {
    $exchange = Invoke-RestMethod -Uri "$lvApiUrlDefault/api/executor/pairing-codes/$PairingCode" -Method Get -TimeoutSec 15
    break
  } catch {
    Say "Kod '$PairingCode' nie zadziałał — mógł wygasnąć (15 min). Próba $attempt z 3."
    if ($attempt -eq 3) {
      Die "Wymiana kodu nieudana po 3 próbach. Wygeneruj nowy kod w LasVegas (Podłącz agenta) i uruchom instalator ponownie."
    }
    $PairingCode = Read-Host "Wklej NOWY kod parowania z LasVegas (Podłącz agenta)"
    if ($PairingCode -notmatch '^[A-Z0-9]{10}$') {
      Die "Kod parowania ma 10 znaków (litery/cyfry, bez 0/O/1/I). Otrzymano: '$PairingCode'"
    }
  }
}
$token = $exchange.token
$apiUrl = if ($exchange.apiUrl) { $exchange.apiUrl } else { $lvApiUrlDefault }
if (-not $token) { Die "Serwer nie zwrócił tokenu. Wygeneruj nowy kod w LasVegas." }

# Smoke test, ZANIM instalator powie „gotowe": token zapisany ≠ token działający.
# Bez tego pierwszy cykl padał dopiero w logu zadania, a użytkownik widział
# świeżo „sparowanego" agenta, który nigdy nic nie postawił.
Say "Testuję token (kill-switch)…"
try {
  Invoke-RestMethod -Uri "$apiUrl/api/executor/kill-switch" -Method Get -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 15 | Out-Null
} catch {
  Die "Token nie działa — kill-switch nie odpowiedział. Sparuj urządzenie ponownie w LasVegas (Podłącz agenta) i uruchom instalator jeszcze raz."
}

# Zapis do .env Hermesa: utwórz / dopisz / podmień linie PER KLUCZ (jak install.sh).
# Reinstalacja podmienia OBA klucze — stary LV_API_URL zostawiony sam kazałby
# agentowi pukać pod nieaktualny adres.
$envFile = Join-Path $hermesHome ".env"
$envText = ""
if (Test-Path $envFile) { $envText = Get-Content -Path $envFile -Raw -Encoding UTF8 }
if (-not $envText) { $envText = "" }
if ($envText -match "(?m)^LV_EXECUTOR_TOKEN=") {
  $envText = [regex]::Replace($envText, "(?m)^LV_EXECUTOR_TOKEN=[^\r\n]*", "LV_EXECUTOR_TOKEN=$token")
  if ($envText -match "(?m)^LV_API_URL=") {
    $envText = [regex]::Replace($envText, "(?m)^LV_API_URL=[^\r\n]*", "LV_API_URL=$apiUrl")
  } else {
    $envText = $envText.TrimEnd([char[]]"`r`n") + "`r`nLV_API_URL=$apiUrl`r`n"
  }
} else {
  $envText = $envText + "`r`nLV_EXECUTOR_TOKEN=$token`r`nLV_API_URL=$apiUrl`r`n"
}
# PowerShell 5.1 domyślnie dokłada BOM w Set-Content/Out-File — piszemy przez
# .NET, żeby .env został czystym UTF-8 bez BOM (Hermes czyta go jako UTF-8).
[IO.File]::WriteAllText($envFile, $envText, (New-Object System.Text.UTF8Encoding($false)))
Say "Token zapisany do $envFile (plik .env Hermesa — nikt go nie wkleja w czat)."

# --- 4.5. Poświadczenia bukmacherów (opcjonalnie, lokalnie) ------------------
# Agent loguje się sam skryptem skilla (scripts/lv-login.py) z pliku
# $hermesHome\lv-bookmakers.env. Hasło zostaje na tym komputerze — nie idzie
# do LasVegas ani do modelu. Pominięcie = agent poprosi o zalogowanie w oknie.
Say "Poświadczenia bukmacherów (opcjonalnie): agent zaloguje się sam, gdy je zapiszesz."
Say "Poprawka później (np. złe hasło): ta sama komenda, nadpisuje wpis bukmachera:"
$credScript = Join-Path $hermesHome "skills\lv-executor\scripts\lv-credentials.ps1"
Write-Host "    & `"$credScript`" sts      (albo: superbet)"
foreach ($credSlug in @("sts", "superbet")) {
  $answer = Read-Host "Zapisać login i hasło do $credSlug? [t/N]"
  if ($answer -notmatch '^(t|tak|y)$') { continue }
  if (-not (Test-Path $credScript)) { Say "Brak $credScript — skill nie zainstalował się poprawnie, pomijam."; break }
  try { & $credScript $credSlug } catch { Say "Poświadczenia $credSlug nie zapisane: $($_.Exception.Message)" }
}

$skillMd = Join-Path $hermesHome "skills\lv-executor\SKILL.md"
$skillVersion = ""
if (Test-Path $skillMd) {
  $versionLine = Select-String -Path $skillMd -Pattern '^version:\s*(.+)$' | Select-Object -First 1
  if ($versionLine) { $skillVersion = $versionLine.Matches[0].Groups[1].Value.Trim() }
}
if ($skillVersion) { Say "Skill lv-executor: v$skillVersion" } else { Say "Skill lv-executor: zainstalowany." }
Say "Sprawdź urządzenie w LasVegas → Podłącz agenta."

# --- 5. Scheduled task co 5 minut --------------------------------------------
Say "Rejestruję uruchamianie agenta co 5 minut + po restarcie komputera…"
$hermesExe = $hermesCmd.Source
$action = New-ScheduledTaskAction -Execute $hermesExe `
  -Argument 'chat --toolsets skills,terminal,browser -q "/lv-executor wykonaj zalegle zlecenia"'
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "LasVegasExecutor" -Action $action -Trigger $trigger `
  -Settings $settings -Force | Out-Null
Say "Scheduled task 'LasVegasExecutor' zarejestrowany."

# --- 6. Pierwsza runda --------------------------------------------------------
Say "Gotowe. Uruchamiam pierwszą rundę agenta…"
Say "Token jest już w .env — agent nie powinien pytać o LV_EXECUTOR_TOKEN."
Say "Jeśli bukmacher poprosi o logowanie — agent otworzy okno i poprosi o zalogowanie RAZ."
& $hermesExe chat --toolsets skills,terminal,browser -q "/lv-executor wykonaj zalegle zlecenia"

Say "Instalacja zakończona. Status i kill switch: LasVegas → Podłącz agenta."
