"""Checkout por QR: leitura do código e cruzamento ML -> Bling (dados fictícios, sem rede)."""
import os
import shutil
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketplaces.checkout import busca, gravacao, leitura, registro
from marketplaces.checkout.router import router

QR_GARBLED = "^id^Ç^48424630205^,^sender_id^Ç1874506744,^hash_code^Çnull,^security_digit^Çnull{"


class TestLeitura(unittest.TestCase):
    def test_qr_com_layout_trocado(self):
        r = leitura.ler(QR_GARBLED)
        self.assertEqual((r["tipo"], r["numero"], r["sender_id"]), ("envio", "48424630205", "1874506744"))

    def test_qr_json_normal(self):
        r = leitura.ler('{"id":"48424033531","sender_id":128975981,"hash_code":null,"security_digit":null}')
        self.assertEqual((r["numero"], r["sender_id"]), ("48424033531", "128975981"))

    def test_sender_id_nao_vira_id(self):
        r = leitura.ler('{"sender_id":1874506744,"id":"48424630205"}')
        self.assertEqual(r["numero"], "48424630205")

    def test_rastreio_mel(self):
        self.assertEqual(leitura.ler("MEL48414186007FMXDF01")["numero"], "48414186007")

    def test_numero_solto_envio_ou_pedido(self):
        self.assertEqual(leitura.ler("48414186007")["tipo"], "envio")
        self.assertEqual(leitura.ler("2000019309814412")["tipo"], "pedido")

    def test_lixo_e_vazio(self):
        self.assertIsNone(leitura.ler(""))
        self.assertIsNone(leitura.ler(None))
        self.assertIsNone(leitura.ler("abc"))


class MLFalso:
    def __init__(self, envios, pedidos):
        self.envios, self.pedidos = envios, pedidos

    def get(self, caminho, **_):
        tipo, _, chave = caminho.strip("/").partition("/")
        base = self.envios if tipo == "shipments" else self.pedidos
        return (200, base[chave]) if chave in base else (403, {})


ENVIO = {"id": 48424630205, "order_id": 2000019331794234, "logistic_type": "self_service", "status": "ready_to_ship",
         "substatus": "printed", "tracking_number": "CZ5U64"}
PEDIDO = {"id": 2000019331794234, "pack_id": None, "shipping": {"id": 48424630205},
          "order_items": [{"item": {"title": "Torneira", "seller_sku": "T1"}, "quantity": 2}]}
LINHA = {"id": 7, "numero": 11621, "numeroLoja": "2000019331794234", "situacao": {"id": 6}}


def rodar(codigo, linha=LINHA, envio=ENVIO, rastreio_bling="CZ5U64", erro_bling=None, seller="1874506744"):
    contas = {"meli02": MLFalso({"48424630205": envio}, {"2000019331794234": PEDIDO})}
    vazio = MLFalso({}, {})

    def pedidos(_conta):
        if erro_bling:
            raise RuntimeError(erro_bling)
        return {linha["numeroLoja"]: linha} if linha else {}

    detalhe = lambda c, i: {"transporte": {"volumes": [{"codigoRastreamento": rastreio_bling}]},
                            "itens": [{"descricao": "Torneira", "codigo": "T1", "quantidade": 2}]}
    return busca.buscar(codigo, ml=lambda c: contas.get(c, vazio), seller=lambda c: seller if c == "meli02" else "0",
                        pedidos_bling=pedidos, detalhe_bling=detalhe)


class TestBusca(unittest.TestCase):
    def test_fluxo_feliz(self):
        r = rodar(QR_GARBLED)
        self.assertEqual((r["conta"], r["nivel"], r["ok"]), ("meli02", "ok", True))
        self.assertEqual(r["bling"]["numero"], 11621)
        self.assertEqual(r["ml"]["envio"], 48424630205)
        self.assertEqual(r["ml"]["canal"], "Flex")
        self.assertIs(r["rastreio_confere"], True)

    def test_acha_a_conta_sem_sender_id(self):
        self.assertEqual(rodar("48424630205")["conta"], "meli02")

    def test_rastreio_diferente_e_vazio(self):
        self.assertIs(rodar(QR_GARBLED, rastreio_bling="OUTRO")["rastreio_confere"], False)
        self.assertIsNone(rodar(QR_GARBLED, rastreio_bling="")["rastreio_confere"])

    def test_ja_verificado_avisa(self):
        r = rodar(QR_GARBLED, linha=dict(LINHA, situacao={"id": 24}))
        self.assertEqual(r["nivel"], "aviso")
        self.assertIn("já feito", r["mensagem"])

    def test_cancelado_bloqueia(self):
        self.assertEqual(rodar(QR_GARBLED, linha=dict(LINHA, situacao={"id": 12}))["nivel"], "bloqueio")
        self.assertEqual(rodar(QR_GARBLED, envio=dict(ENVIO, status="cancelled"))["nivel"], "bloqueio")

    def test_pedido_fora_do_bling_avisa(self):
        r = rodar(QR_GARBLED, linha=None)
        self.assertEqual(r["nivel"], "aviso")
        self.assertIsNone(r["bling"])

    def test_bling_fora_do_ar_nao_derruba(self):
        r = rodar(QR_GARBLED, erro_bling="HTTP 500")
        self.assertEqual(r["nivel"], "aviso")
        self.assertTrue(r["ml"]["envio"])

    def test_envio_inexistente_e_codigo_ruim(self):
        self.assertFalse(rodar("48400000000")["ok"])
        self.assertFalse(rodar("abc")["ok"])


class TestRota(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.antes = registro.ARQUIVO
        registro.ARQUIVO = os.path.join(self.pasta, "reg.jsonl")   # nunca escrever no registro real

    def tearDown(self):
        registro.ARQUIVO = self.antes
        shutil.rmtree(self.pasta, ignore_errors=True)

    def test_pagina_e_rota(self):
        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        self.assertIn("Checkout de etiquetas", c.get("/checkout").text)
        r = c.post("/checkout/buscar", json={"codigo": "abc"}).json()
        self.assertFalse(r["ok"])
        self.assertEqual(r["acao_prevista"], "nenhuma")
        self.assertEqual(len(registro.ler()), 1)

    def test_modulo_nao_tem_codigo_de_gravacao_no_bling(self):
        pasta = os.path.dirname(busca.__file__)
        for nome in os.listdir(pasta):
            if nome.endswith(".py") and nome != "gravacao.py":   # gravacao.py é o único ponto de escrita
                with open(os.path.join(pasta, nome), encoding="utf-8") as f:
                    codigo = f.read()
                for proibido in ("bling.post", "bling.put", "bling.patch", "bling.delete", ".put(", ".patch(", ".delete("):
                    self.assertNotIn(proibido, codigo, f"{nome}: {proibido}")


SITS = {"data": [{"id": 6, "nome": "Em aberto"}, {"id": 24, "nome": "Verificado"}]}


def pedido_bling(sit=6, obs="TESTE CHECKOUT MZ - cancelar depois"):
    return {"data": {"numero": 2427, "situacao": {"id": sit}, "observacoesInternas": obs, "total": 239.9,
                     "contato": {"nome": "TESTE"},
                     "itens": [{"codigo": "KIT", "descricao": "Kit", "quantidade": 1, "produto": {"id": 99}}]}}


class GetFalso:
    def __init__(self, antes=6, depois=24, obs="TESTE CHECKOUT MZ - cancelar depois"):
        self.chamadas, self.antes, self.depois, self.obs, self.leituras = [], antes, depois, obs, 0

    def __call__(self, conta, caminho, **_):
        self.chamadas.append(caminho)
        if caminho.startswith("/situacoes"):
            return 200, SITS
        if caminho == "/estoques/saldos":
            fis = 0 if self.leituras <= 1 else -1
            return 200, {"data": [{"saldoFisicoTotal": fis, "saldoVirtualTotal": -1,
                                   "depositos": [{"id": 1, "saldoFisico": fis, "saldoVirtual": -1}]}]}
        if caminho == "/pedidos/vendas" and "numero" in _:
            return 200, {"data": [{"id": 27304166639, "numero": int(_["numero"])}]}
        self.leituras += 1
        return 200, pedido_bling(self.antes if self.leituras == 1 else self.depois, self.obs)


class TestGravacao(unittest.TestCase):
    def setUp(self):
        self.log = os.path.join(tempfile.mkdtemp(), "g.jsonl")
        self.enviados = []

    def enviar(self, conta, metodo, caminho):
        self.enviados.append((conta, metodo, caminho))
        return 200, {}

    def rodar(self, get=None, simular=True, ligado=True):
        return gravacao.mudar_situacao("bling03", 27304166639, "Verificado", simular, get=get or GetFalso(),
                                       enviar=self.enviar, interruptor=lambda: ligado, arquivo_log=self.log)

    def test_simulacao_nao_envia_nada(self):
        r = self.rodar()
        self.assertFalse(r["enviado"])
        self.assertEqual(self.enviados, [])
        self.assertEqual(r["estoque_antes"][0]["codigo"], "KIT")
        self.assertEqual(r["itens"][0]["quantidade"], 1)
        self.assertEqual(r["requisicao"], "PATCH /pedidos/vendas/27304166639/situacoes/24")
        self.assertFalse(os.path.exists(self.log))

    def test_grava_e_confirma(self):
        r = self.rodar(simular=False)
        self.assertEqual(self.enviados, [("bling03", "PATCH", "/pedidos/vendas/27304166639/situacoes/24")])
        self.assertTrue(r["confirmado"])
        self.assertEqual((r["de"], r["para"], r["situacao_depois"]), (6, 24, 24))
        self.assertIsNotNone(r["estoque_antes"])
        self.assertIsNotNone(r["estoque_depois"])
        self.assertEqual(len(open(self.log, encoding="utf-8").readlines()), 1)

    def test_nao_confirma_se_a_situacao_nao_mudou(self):
        r = self.rodar(get=GetFalso(depois=6), simular=False)
        self.assertFalse(r["confirmado"])

    def test_trava_de_pedido_de_teste(self):
        with self.assertRaises(gravacao.ErroGravacao):
            self.rodar(get=GetFalso(obs="pedido real"), simular=False)
        self.assertEqual(self.enviados, [])

    def test_interruptor_desligado(self):
        with self.assertRaises(gravacao.ErroGravacao):
            self.rodar(simular=False, ligado=False)
        self.assertEqual(self.enviados, [])

    def test_so_marca_se_estiver_em_aberto(self):
        with self.assertRaises(gravacao.ErroGravacao):
            self.rodar(get=GetFalso(antes=24), simular=False)
        self.assertEqual(self.enviados, [])

    def test_interruptor_exige_validade(self):
        from datetime import datetime, timedelta
        d = tempfile.mkdtemp()
        p = os.path.join(d, "c.json")
        agora = datetime.now(gravacao.BRT)

        def escrever(texto):
            with open(p, "w", encoding="utf-8") as f:
                f.write(texto)
        self.assertFalse(gravacao.interruptor_ligado(p))                      # arquivo não existe
        escrever('{"gravacao": true}')
        self.assertFalse(gravacao.interruptor_ligado(p))                      # sem "ate": desligado
        escrever('{"gravacao": true, "ate": "%s"}' % (agora + timedelta(minutes=30)).isoformat())
        self.assertTrue(gravacao.interruptor_ligado(p))
        escrever('{"gravacao": true, "ate": "%s"}' % (agora - timedelta(minutes=1)).isoformat())
        self.assertFalse(gravacao.interruptor_ligado(p))                      # venceu
        escrever('{"gravacao": "sim", "ate": "%s"}' % (agora + timedelta(minutes=30)).isoformat())
        self.assertFalse(gravacao.interruptor_ligado(p))

    def test_achar_pedido_por_numero(self):
        self.assertEqual(gravacao.achar_pedido("bling03", 2427, get=GetFalso()), 27304166639)
        with self.assertRaises(gravacao.ErroGravacao):
            gravacao.achar_pedido("bling03", 1, get=lambda *a, **k: (200, {"data": []}))

    def test_rota_confirmar_e_simulacao_por_padrao(self):
        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        r = c.post("/checkout/confirmar", json={"bling_conta": "outra", "pedido_id": 1})
        self.assertEqual(r.status_code, 400)


class TestSemEstoque(unittest.TestCase):
    """Estoque é sensível: o módulo nunca lança nem estorna estoque. Só a situação do pedido pode mudar (gravacao.py)."""

    def test_nenhum_arquivo_do_modulo_mexe_em_estoque(self):
        pasta = os.path.dirname(busca.__file__)
        for nome in os.listdir(pasta):
            if nome.endswith(".py"):
                with open(os.path.join(pasta, nome), encoding="utf-8") as f:
                    codigo = f.read().lower()
                codigo = codigo.replace("/estoques/saldos", "")   # leitura de saldo é permitida
                for proibido in ("estornar-estoque", "lancar-estoque", "lançar-estoque", "/estoques"):
                    self.assertNotIn(proibido, codigo, f"{nome}: {proibido}")

    def test_unica_escrita_e_o_patch_de_situacao(self):
        with open(os.path.join(os.path.dirname(busca.__file__), "gravacao.py"), encoding="utf-8") as f:
            codigo = f.read()
        self.assertIn('"PATCH"', codigo)
        for proibido in ('"POST"', '"PUT"', '"DELETE"', "httpx.post", "httpx.put", "httpx.delete"):
            self.assertNotIn(proibido, codigo, proibido)


def entrada(**kw):
    base = {"ts": "2026-11-05T09:00:00-03:00", "bling_conta": "bling02", "numeroLoja": "A", "envio": 1, "pedido": "A",
            "conta": "meli02", "canal": "Flex", "bling_numero": 10, "situacao_id": 6, "nivel": "ok",
            "acao": "marcar_verificado", "mensagem": "ok"}
    base.update(kw)
    return base


class TestSombra(unittest.TestCase):
    def aval(self, entradas, agora):
        return registro.avaliar(entradas, lambda c, n: agora.get(n) if isinstance(agora, dict) else agora)

    def test_acao_prevista(self):
        ok = {"nivel": "ok", "bling": {"situacao_id": 6}}
        self.assertEqual(registro.acao_prevista(ok), "marcar_verificado")
        self.assertEqual(registro.acao_prevista({"nivel": "ok", "bling": {"situacao_id": 9}}), "nenhuma")
        self.assertEqual(registro.acao_prevista({"nivel": "aviso", "bling": {"situacao_id": 24}}), "nenhuma")
        self.assertEqual(registro.acao_prevista({"nivel": "bloqueio", "bling": {"situacao_id": 12}}), "nenhuma")
        self.assertEqual(registro.acao_prevista({"nivel": "aviso", "bling": None}), "nenhuma")

    def test_previu_e_estoquista_fez(self):
        r = self.aval([entrada()], 24)
        self.assertEqual(r["contagem"], {"confere": 1})
        self.assertEqual(r["assertividade_pct"], 100.0)

    def test_previu_e_ainda_aberto_fica_pendente(self):
        r = self.aval([entrada()], 6)
        self.assertEqual(len(r["pendentes"]), 1)
        self.assertIsNone(r["assertividade_pct"])

    def test_bloqueado_mas_despachado_diverge(self):
        r = self.aval([entrada(nivel="bloqueio", acao="nenhuma", mensagem="Cancelado")], 24)
        self.assertEqual(r["contagem"], {"divergente": 1})
        self.assertEqual(r["assertividade_pct"], 0.0)
        self.assertEqual(len(r["precisam_olhar"]), 1)

    def test_sem_pedido_no_bling_precisa_olhar(self):
        r = self.aval([entrada(bling_numero=None, numeroLoja=None, nivel="aviso", acao="nenhuma")], None)
        self.assertEqual(r["contagem"], {"sem_pedido": 1})

    def test_so_a_primeira_leitura_conta(self):
        r = self.aval([entrada(), entrada(ts="2026-11-05T09:05:00-03:00", nivel="aviso", acao="nenhuma")], 24)
        self.assertEqual((r["leituras"], r["pedidos"], r["contagem"]), (2, 1, {"confere": 1}))

    def test_mistura(self):
        entradas = [entrada(numeroLoja="A"), entrada(numeroLoja="B"), entrada(numeroLoja="C", nivel="bloqueio", acao="nenhuma")]
        r = self.aval(entradas, {"A": 24, "B": 24, "C": 24})
        self.assertEqual(r["contagem"], {"confere": 2, "divergente": 1})
        self.assertEqual(r["assertividade_pct"], 66.7)

    def test_gravar_nao_derruba(self):
        registro.gravar({"nivel": "ok"}, "x", arquivo=os.path.join(tempfile.gettempdir(), "nao_existe_dir", "a", "b.jsonl"))


if __name__ == "__main__":
    unittest.main()
