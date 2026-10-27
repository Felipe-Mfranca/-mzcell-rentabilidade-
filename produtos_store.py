"""
produtos_store.py — Único ponto de leitura e gravação do produtos.json
MZ App

Problemas que isto resolve:
- Gravação direta (open 'w') apaga o arquivo antes de escrever: outra leitura pega JSON pela metade.
- Windows bloqueia o arquivo quando outro processo o abre (antivírus, OneDrive, visualização): a gravação falhava.
- Dois trechos gravavam o arquivo inteiro a partir de cópias antigas: um apagava o que o outro acabou de salvar.

Regras:
- Gravação sempre atômica (arquivo temporário + troca), com nova tentativa quando o Windows bloqueia.
- Toda alteração passa por `alterar()` ou `mesclar()`, sob a mesma trava: lê o estado atual, muda só o que precisa.
"""

import json
import os
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE_DIR, "produtos.json")

_TRAVA = threading.RLock()
_TENTATIVAS = 15
_PAUSA_S = 0.2


def ler() -> dict:
    """Lê o arquivo inteiro. Tenta de novo se o arquivo estiver bloqueado ou sendo trocado."""
    ultimo = None
    for _ in range(_TENTATIVAS):
        try:
            with _TRAVA:
                if not os.path.exists(PATH):
                    return {}
                with open(PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (PermissionError, json.JSONDecodeError, FileNotFoundError) as e:
            ultimo = e
            time.sleep(_PAUSA_S)
    raise RuntimeError(f"produtos.json não pôde ser lido: {type(ultimo).__name__}")


def _gravar_atomico(dados: dict):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    ultimo = None
    for _ in range(_TENTATIVAS):
        try:
            os.replace(tmp, PATH)
            return
        except PermissionError as e:
            ultimo = e
            time.sleep(_PAUSA_S)
    raise RuntimeError(f"produtos.json não pôde ser gravado (bloqueado): {ultimo}")


def alterar(funcao):
    """Lê o estado atual, aplica `funcao(dados)` (que muda o dicionário in-place) e grava. Devolve o retorno da função."""
    with _TRAVA:
        dados = ler()
        resultado = funcao(dados)
        _gravar_atomico(dados)
        return resultado


def mesclar(mudados: dict):
    """Grava só os produtos informados ({mlb: produto}); os demais continuam como estão no arquivo."""
    if not mudados:
        return
    def aplicar(dados):
        dados.update(mudados)
    alterar(aplicar)
