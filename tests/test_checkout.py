"""Checkout por QR: leitura do código e cruzamento ML -> Bling (dados fictícios, sem rede)."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketplaces.checkout import busca, leitura
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
    def test_pagina_e_rota(self):
        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        self.assertIn("Checkout de etiquetas", c.get("/checkout").text)
        self.assertFalse(c.post("/checkout/buscar", json={"codigo": "abc"}).json()["ok"])


if __name__ == "__main__":
    unittest.main()
