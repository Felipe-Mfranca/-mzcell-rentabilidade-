<#
  Instala (ou remove) a tarefa agendada da agenda de rebot no Windows.
  A agenda (rebot_agenda.py) so le o Mercado Livre e avisa por e-mail/Slack; nao escreve no ML.

  Uso:
    .\instalar_agenda.ps1                              # 08:00 e 16:00, todos os dias
    .\instalar_agenda.ps1 -Horarios "09:00","13:00","18:00"
    .\instalar_agenda.ps1 -Remover                     # apaga a tarefa

  Roda no usuario atual (sem pedir senha). Se o PC estiver desligado no horario, a tarefa
  executa assim que ele ligar. Reexecutar o script substitui a tarefa existente.
#>
param(
    [string[]]$Horarios = @("08:00", "16:00"),
    [switch]$Remover,
    [string]$Nome = "MZCell Rebot Agenda"
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
$script = Join-Path $raiz "rebot_agenda.py"
if (-not (Test-Path $py)) { throw "Python do venv nao encontrado: $py (crie o venv e instale o requirements.txt)" }
if (-not (Test-Path $script)) { throw "rebot_agenda.py nao encontrado em $raiz" }
foreach ($h in $Horarios) {
    if ($h -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "Horario invalido: '$h' (use HH:mm, ex.: 08:00)" }
}

$acao = New-ScheduledTaskAction -Execute $py -Argument "rebot_agenda.py" -WorkingDirectory $raiz
$gatilhos = @($Horarios | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger $gatilhos -Settings $config `
    -Description "Verifica promocoes/rebot das contas ML e avisa (somente leitura)" -Force | Out-Null

$t = Get-ScheduledTask -TaskName $Nome
Write-Host "Tarefa '$Nome' instalada. Horarios: $($Horarios -join ', ')"
Write-Host "Proxima execucao: $((Get-ScheduledTaskInfo -TaskName $Nome).NextRunTime)"
Write-Host "Teste manual:  .\venv\Scripts\python.exe rebot_agenda.py --dry"
