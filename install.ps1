# Instalator stacjonarnego agenta LasVegas (Hermes + skill lv-executor) — Windows.
# Użycie: irm https://raw.githubusercontent.com/PNowakBG/lasvegas-skills/main/install.ps1 | iex
# (kod parowania poda użytkownik przy pierwszym uruchomieniu skilla — Herms zapyta w oknie czatu)

$ErrorActionPreference = "Stop"
$hermesHome = Join-Path $env:USERPROFILE ".hermes"

function Say($msg) { Write-Host "`n[lv] $msg" -ForegroundColor Cyan }

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

# --- 2. Config przeglądarki (real-profile, headed, nagrywanie) ---------------
Say "Konfiguruję przeglądarkę agenta (Twój profil, widoczne okno, nagrywanie sesji)…"
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
  record_sessions: true
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

# --- 4. Kod parowania → token (na Windows pyta Herms przy pierwszym użyciu) --
Say "Na Windows kod parowania podajesz przy pierwszym uruchomieniu skilla:"
Say "Hermes zapyta w oknie czatu o LV_EXECUTOR_TOKEN — wklej kod z LasVegas."
Say "(Instalator czeka na pierwszy bieg i od razu prosi o kod — patrz krok 6.)"

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
Say "Jeśli pojawi się pytanie o LV_EXECUTOR_TOKEN — wklej kod parowania z LasVegas."
Say "Jeśli bukmacher poprosi o logowanie — agent otworzy okno i poprosi o zalogowanie RAZ."
& $hermesExe chat --toolsets skills,terminal,browser -q "/lv-executor wykonaj zalegle zlecenia"

Say "Instalacja zakończona. Status i kill switch: LasVegas → Podłącz agenta."
