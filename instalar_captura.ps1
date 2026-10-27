<#
  Instala (ou remove) a tarefa diaria de captura de pedidos e reclamacoes do ML (captura_pedidos_ml.py).
  A captura so le a API do Mercado Livre e grava em financeiro.db.

  Uso:
    .\instalar_captura.ps1                  # 09:00 todos os dias, meli01
    .\instalar_captura.ps1 -Remover         # apaga a tarefa

  Se o PC estiver desligado as 09:00, a tarefa executa assim que ele ligar.
  Resultado de cada execucao: captura_log.txt
#>
param(
    [string]$Horario = "09:00",
    [string[]]$Contas = @("meli01"),
    [switch]$Remover,
    [string]$Nome = "MZCell Captura Pedidos ML"
)
$ErrorActionPreference = "Stop"

if ($Remover) {
    if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
        Write-Host "Tarefa '$Nome' removida."
    } else {
        Write-Host "Tarefa '$Nome' nao existe."
    }
    exit 0
}

$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $raiz "venv\Scripts\python.exe"
$script = Join-Path $raiz "captura_pedidos_ml.py"
if (-not (Test-Path $py)) { throw "Python do venv nao encontrado: $py" }
if (-not (Test-Path $script)) { throw "captura_pedidos_ml.py nao encontrado em $raiz" }
if ($Horario -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "Horario invalido: '$Horario' (use HH:mm)" }

$pyw = Join-Path $raiz "venv\Scripts\pythonw.exe"
if (Test-Path $pyw) { $exe = $pyw } else { $exe = $py }
$argumentos = "captura_pedidos_ml.py " + ($Contas -join " ")
$acao = New-ScheduledTaskAction -Execute $exe -Argument $argumentos -WorkingDirectory $raiz
$gatilho = New-ScheduledTaskTrigger -Daily -At $Horario
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 60)

Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger $gatilho -Settings $config `
    -Description "Captura diaria de pedidos e reclamacoes do ML (somente leitura) para o financeiro" -Force | Out-Null

Write-Host "Tarefa '$Nome' instalada: $Horario todos os dias, contas: $($Contas -join ', ')"
Write-Host "Proxima execucao: $((Get-ScheduledTaskInfo -TaskName $Nome).NextRunTime)"
Write-Host "Log: captura_log.txt"
