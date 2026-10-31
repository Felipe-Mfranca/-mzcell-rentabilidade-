# MZ App - menu de manutencao seguro (para quem nao tem a pratica do dia a dia)
# Uso:  .\scripts\mz.ps1            (abre o menu)
#       .\scripts\mz.ps1 estado     (roda uma opcao direto: estado, iniciar, reiniciar, erros, testes, dados, backup)
# Cada opcao diz se so LE (verde) ou se ALTERA algo (amarelo). Nenhuma opcao envia nada para o Mercado Livre,
# Amazon ou Bling. Publicar no main so acontece pela opcao 12 (testes + checklist) e e desfeito pela 13.

$ErrorActionPreference = "Continue"
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz
$Py = Join-Path $Raiz "venv\Scripts\python.exe"
$LogOut = Join-Path $env:TEMP "uvicorn_out.log"
$LogErr = Join-Path $env:TEMP "uvicorn_err.log"
$Porta = 8001

function Ler($t)   { Write-Host "  [SO LE]  $t" -ForegroundColor Green }
function Altera($t){ Write-Host "  [ALTERA] $t" -ForegroundColor Yellow }
function Aviso($t) { Write-Host $t -ForegroundColor Red }
function Pausa     { Write-Host ""; Read-Host "Enter para voltar ao menu" | Out-Null }

function Servidor-Pid {
    $c = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.OwningProcess } else { return $null }
}

function Estado {
    Ler "mostra se o app esta no ar, a versao, a branch e o que foi alterado"
    $p = Servidor-Pid
    if ($p) { Write-Host "Servidor: NO AR (porta $Porta, processo $p)" -ForegroundColor Green } else { Aviso "Servidor: PARADO (use a opcao Iniciar)" }
    Write-Host ("Versao do app: " + (Get-Content (Join-Path $Raiz "version.txt") -ErrorAction SilentlyContinue))
    Write-Host ("Branch atual: " + (git rev-parse --abbrev-ref HEAD))
    Write-Host "Arquivos alterados (nao salvos):"
    git status --short | Where-Object { $_ -notmatch "\.tmp$|node_modules|__pycache__" } | Select-Object -First 25
    if ($p) {
        try { $r = Invoke-RestMethod "http://127.0.0.1:$Porta/sistema/versao" -TimeoutSec 5; Write-Host "App respondeu: OK" -ForegroundColor Green } catch { Aviso "App nao respondeu em /sistema/versao" }
    }
}

function Iniciar {
    Altera "liga o servidor do app (nao mexe em dados)"
    if (Servidor-Pid) { Write-Host "Ja esta no ar. Use Reiniciar se mudou algum arquivo .py."; return }
    Start-Process -FilePath $Py -ArgumentList "-m","uvicorn","main:app","--host","127.0.0.1","--port","$Porta" -WorkingDirectory $Raiz -WindowStyle Hidden -RedirectStandardOutput $LogOut -RedirectStandardError $LogErr
    Write-Host "Ligando... espere uns 60 segundos (o app carrega o Bling no inicio)."
    Start-Sleep 8
    if (Servidor-Pid) { Write-Host "No ar: http://127.0.0.1:$Porta" -ForegroundColor Green } else { Aviso "Nao subiu. Veja a opcao Erros do servidor." }
}

function Parar {
    $p = Servidor-Pid
    if ($p) { Stop-Process -Id $p -Force; Start-Sleep 2; Write-Host "Servidor parado." } else { Write-Host "Ja estava parado." }
}

function Reiniciar {
    Altera "desliga e liga o servidor (necessario depois de mudar qualquer arquivo .py; o dashboard.html nao precisa)"
    Parar
    Iniciar
}

function Erros {
    Ler "mostra as ultimas linhas do registro de erros do servidor"
    if (Test-Path $LogErr) { Get-Content $LogErr -Tail 40 } else { Write-Host "Sem arquivo de erros ($LogErr)." }
}

function Testes {
    Ler "roda os testes automaticos (nao usa internet nem credenciais). Tudo OK = pode salvar a mudanca"
    & $Py -m unittest discover -s tests -p "test_*.py"
    if ($LASTEXITCODE -eq 0) { Write-Host "TESTES OK" -ForegroundColor Green } else { Aviso "TESTES FALHARAM - nao salve a mudanca. Copie esta tela e mostre ao Claude." }
}

function Dados {
    Ler "confere se os arquivos de dados (.json) estao validos"
    & $Py verificar_dados.py
}

function Backup {
    Altera "copia os arquivos de dados para a pasta backups\ (nao apaga nada)"
    $dest = Join-Path $Raiz ("backups\" + (Get-Date -Format "yyyy-MM-dd_HHmm"))
    New-Item -ItemType Directory -Force $dest | Out-Null
    $lista = "produtos.json","cmv_base.json","impostos.json","config.json","bling_config.json","amazon_config.json","amazon_custos.json","amazon_asin_skus.json","anotacoes.json","posicoes.json","pivo_manual.json","amazon_pivo.json","usuarios.json","tags_compartilhadas.json","marcacao_expedicao.json","shared_config.json"
    foreach ($f in $lista) { if (Test-Path (Join-Path $Raiz $f)) { Copy-Item (Join-Path $Raiz $f) $dest } }
    Write-Host "Backup salvo em $dest" -ForegroundColor Green
    Write-Host "ATENCAO: ele contem senhas e tokens. Nao envie para ninguem e nao suba para o GitHub."
}

function Comecar {
    Altera "cria uma branch propria para a sua mudanca (o main nao e tocado)"
    $atual = git rev-parse --abbrev-ref HEAD
    if ($atual -ne "main") { Aviso "Voce ja esta na branch '$atual'. Termine ou desfaca ela antes (opcoes Salvar / Desfazer)."; return }
    $quem = Read-Host "Seu nome (ex: michel)"
    $oque = Read-Host "O que vai mudar, em 2-3 palavras (ex: ajuste-coluna-sku)"
    $nome = ("mz/" + $quem + "-" + $oque + "-" + (Get-Date -Format "MMdd")) -replace "[^a-zA-Z0-9/_-]", "-"
    Backup
    git checkout -b $nome
    Write-Host "Pronto. Voce esta na branch $nome. Agora peca a mudanca ao Claude." -ForegroundColor Green
}

function Mudancas {
    Ler "mostra o que foi alterado desde a ultima versao salva"
    git status --short | Where-Object { $_ -notmatch "\.tmp$|node_modules|__pycache__" }
    git diff --stat
}

function Salvar {
    Altera "salva a sua mudanca na SUA branch (roda os testes antes; nao vai para o main)"
    $b = git rev-parse --abbrev-ref HEAD
    if ($b -eq "main") { Aviso "Voce esta no main. Use 'Comecar uma mudanca' primeiro."; return }
    & $Py -m unittest discover -s tests -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { Aviso "Testes falharam. Nada foi salvo."; return }
    $msg = Read-Host "Descreva a mudanca em uma frase (comece com feat: , fix: ou docs: )"
    git add -A -- . ":(exclude)node_modules" ":(exclude)*.tmp"
    git status --short
    $ok = Read-Host "Os arquivos acima estao certos? Nenhum .json de dados, senha ou token? (s/n)"
    if ($ok -ne "s") { git reset | Out-Null; Write-Host "Cancelado. Nada foi salvo."; return }
    git commit -m $msg
    Write-Host "Salvo na branch $b." -ForegroundColor Green
}

function Enviar {
    Altera "envia a SUA branch para o GitHub para outra pessoa revisar (nunca o main)"
    $b = git rev-parse --abbrev-ref HEAD
    if ($b -eq "main") { Aviso "Recusado: nao se envia o main por aqui."; return }
    git push -u origin $b
    Write-Host "Branch enviada. Avise o revisor (Slack) com: o que mudou, como testar, e o que pode ser afetado." -ForegroundColor Green
}

function Publicar {
    Altera "coloca a sua mudanca no main (a versao oficial) e envia ao GitHub. So com testes OK e checklist"
    $b = git rev-parse --abbrev-ref HEAD
    if ($b -eq "main") { Aviso "Voce esta no main. Publique a partir da sua branch."; return }
    if (git status --porcelain --untracked-files=no) { Aviso "Ha alteracoes nao salvas. Use 'Salvar minha mudanca' antes."; return }
    Write-Host "Checklist (responda s/n):"
    $itens = "Testei a tela/funcao que mudei no navegador e funcionou?",
             "Rodei os testes e passaram?",
             "A mudanca NAO envia nada ao Mercado Livre/Amazon/Bling sem confirmacao explicita?",
             "NAO tem senha, token ou arquivo .json de dados na mudanca?",
             "Se algo quebrar, eu sei desfazer (opcao 13)?"
    foreach ($i in $itens) { if ((Read-Host $i) -ne "s") { Aviso "Checklist incompleto. Nada foi publicado."; return } }
    & $Py -m unittest discover -s tests -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { Aviso "Testes falharam. Nada foi publicado."; return }
    git checkout main
    git pull origin main
    if ($LASTEXITCODE -ne 0) { Aviso "Conflito ou erro ao atualizar o main. Voltando para a sua branch."; git checkout $b; return }
    $tag = "mz-bom-" + (Get-Date -Format "yyyyMMdd-HHmm")
    git tag $tag
    git merge --no-ff $b -m ("merge: " + $b)
    if ($LASTEXITCODE -ne 0) { git merge --abort; git checkout $b; Aviso "Conflito ao juntar. Nada foi publicado."; return }
    & $Py -m unittest discover -s tests -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { git reset --hard $tag; git checkout $b; Aviso "Falhou depois de juntar. O main foi devolvido ao ponto anterior ($tag)."; return }
    git push origin main
    if ($LASTEXITCODE -ne 0) { Aviso "Nao consegui enviar ao GitHub. O main local esta atualizado; tente de novo ou peca ajuda."; return }
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm') | $env:USERNAME | $b | ponto de volta: $tag" | Add-Content (Join-Path $Raiz "docs\REGISTRO_DE_MUDANCAS.txt")
    Write-Host "Publicado. Ponto de volta: $tag. Reinicie o servidor (opcao 7) e avise o grupo no Slack." -ForegroundColor Green
}

function Reverter {
    Altera "desfaz uma mudanca JA publicada no main, criando um commit que a anula (nao apaga historico)"
    git checkout main | Out-Null
    git pull origin main
    Write-Host "Ultimas publicacoes:"
    git log --oneline -12
    $h = Read-Host "Codigo (primeira coluna) da publicacao a desfazer"
    if (-not $h) { return }
    if ((Read-Host "Desfazer $h ? (s/n)") -ne "s") { return }
    git revert --no-edit $h
    if ($LASTEXITCODE -ne 0) { git revert --abort; Aviso "Nao deu para desfazer sozinho (conflito). Peca ajuda ao Claude com esta tela."; return }
    & $Py -m unittest discover -s tests -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { Aviso "Testes falharam depois de desfazer. Nao enviei ao GitHub; mostre ao Claude." ; return }
    git push origin main
    Write-Host "Desfeito e enviado. Reinicie o servidor (opcao 7)." -ForegroundColor Green
}

function Desfazer {
    Altera "abandona a mudanca atual e volta ao main como estava (a branch fica guardada)"
    $b = git rev-parse --abbrev-ref HEAD
    if ($b -eq "main") { Write-Host "Voce ja esta no main. Para desfazer algo ja publicado, peca ajuda ao revisor (git revert)."; return }
    $ok = Read-Host "Voltar ao main? Alteracoes NAO salvas serao guardadas num stash (s/n)"
    if ($ok -ne "s") { return }
    git stash push -u -m ("mz-desfazer-" + (Get-Date -Format "MMdd_HHmm")) | Out-Null
    git checkout main
    Write-Host "De volta ao main. Reinicie o servidor (opcao Reiniciar)." -ForegroundColor Green
}

function Bling {
    Altera "reautoriza uma conta do Bling (abre o navegador; use o navegador da empresa)"
    Write-Host "Techseller=bling01 (Chrome) | Seller Info=bling02 (Edge) | Mizi=bling03 (Firefox)"
    $c = Read-Host "Qual conta? (bling01, bling02 ou bling03)"
    if ($c -notin "bling01","bling02","bling03") { Aviso "Conta invalida."; return }
    $env:PYTHONIOENCODING = "utf-8"
    if ($c -eq "bling02") { $edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"; if (Test-Path $edge) { $env:BROWSER = $edge } }
    & $Py -m marketplaces.bling.auth $c
}

function Abrir { Start-Process "http://127.0.0.1:$Porta" }

function Menu {
    while ($true) {
        Clear-Host
        Write-Host "=== MZ App - manutencao ===" -ForegroundColor Cyan
        Write-Host " VER (nao altera nada)"
        Write-Host "  1  Estado do app"
        Write-Host "  2  Erros do servidor"
        Write-Host "  3  Rodar testes"
        Write-Host "  4  Conferir arquivos de dados"
        Write-Host "  5  O que eu mudei"
        Write-Host " LIGAR / DESLIGAR"
        Write-Host "  6  Iniciar servidor"
        Write-Host "  7  Reiniciar servidor (depois de mudar .py)"
        Write-Host "  8  Abrir o app no navegador"
        Write-Host " FAZER UMA MUDANCA (seguro)"
        Write-Host "  9  Comecar uma mudanca (faz backup + cria sua branch)"
        Write-Host " 10  Salvar minha mudanca (roda testes)"
        Write-Host " 11  Enviar para um colega conferir (opcional)"
        Write-Host " 12  PUBLICAR no main (testes + checklist)"
        Write-Host " 13  Desfazer algo JA publicado"
        Write-Host " 14  Abandonar a mudanca atual e voltar ao main"
        Write-Host " OUTROS"
        Write-Host " 15  Backup dos dados"
        Write-Host " 16  Reautorizar um Bling"
        Write-Host "  0  Sair"
        $o = Read-Host "Opcao"
        Clear-Host
        switch ($o) {
            "1" { Estado } "2" { Erros } "3" { Testes } "4" { Dados } "5" { Mudancas }
            "6" { Iniciar } "7" { Reiniciar } "8" { Abrir }
            "9" { Comecar } "10" { Salvar } "11" { Enviar } "12" { Publicar } "13" { Reverter } "14" { Desfazer }
            "15" { Backup } "16" { Bling }
            "0" { return }
            default { Aviso "Opcao invalida." }
        }
        Pausa
    }
}

switch ($args[0]) {
    "estado" { Estado } "iniciar" { Iniciar } "reiniciar" { Reiniciar } "erros" { Erros }
    "testes" { Testes } "dados" { Dados } "backup" { Backup }
    default { Menu }
}
