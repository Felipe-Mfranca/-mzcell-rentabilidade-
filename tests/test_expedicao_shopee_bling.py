"""Shopee pelo Bling na Expedição: Bling simulado e arquivo temporário, sem rede."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from marketplaces.expedicao import shopee_bling as s

BRT = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 6, 9, 0, tzinfo=BRT)
SERVICO = {1: "Shopee Xpress", 2: "Logistica Shopee", 3: "Shopee Xpress", 4: "Logistica Shopee", 5: "Outra Coisa"}


def ped(pid, sit, data="2026-11-05", loja=10):
    return {"id": pid, "numero": 1000 + pid, "numeroLoja": f"S{pid}", "data": data, "loja": {"id": loja}, "situacao": {"id": sit}}


class Linhas(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.caminho = os.path.join(d.name, "estado.json")
        self.chamadas = []
        self.pedidos = {}
        for alvo, nome, v in ((s, "CAMINHO", self.caminho), (s.bling, "get", self.get), (s.time, "sleep", lambda x: None),
                              (s.bling_checkout, "_pedidos", lambda c: self.pedidos), (s.cortes, "carregar", lambda: [])):
            p = mock.patch.object(alvo, nome, v); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(s._lojas, {"ts": 0.0, "ids": set()}); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(s._detalhes, clear=True); p.start(); self.addCleanup(p.stop)

    def get(self, conta, path, **k):
        self.chamadas.append(path)
        if path == "/canais-venda":
            return 200, {"data": [{"id": 10, "descricao": "14- Shopee CNPJ"}, {"id": 11, "descricao": "02- MERCADO LIVRE"}]}
        pid = int(path.rsplit("/", 1)[1])
        return 200, {"data": {"transporte": {"volumes": [{"servico": SERVICO[pid]}]}, "itens": [{"codigo": "SKU", "descricao": "Produto", "quantidade": 2}]}}

    def carregar(self, *lista):
        self.pedidos = {p["numeroLoja"]: p for p in lista}
        return s.linhas(AGORA)

    def test_aberto_entra_com_modalidade_e_outra_loja_fica_de_fora(self):
        ls, erros = self.carregar(ped(1, 6), ped(2, 15), ped(3, 6, loja=11))
        self.assertEqual(erros, {})
        self.assertEqual([(l["pedido"], l["canal"], l["bling_checkout"], l["unidades"], l["conta"]) for l in ls],
                         [("S1", "Shopee Xpress", False, 2, "shopee"), ("S2", "Entrega Direta", False, 2, "shopee")])
        self.assertEqual(ls[0]["meta"], "2026-11-06T12:00:00-03:00")
        self.assertEqual(ls[0]["itens"][0]["sku"], "SKU")

    def test_verificado_conta_no_dia_em_que_virou(self):
        self.carregar(ped(1, 6))
        ls, _ = self.carregar(ped(1, 24))                      # estava em aberto: feito hoje, mesmo com pedido de ontem
        self.assertEqual([(l["pedido"], l["bling_checkout"]) for l in ls], [("S1", True)])
        ls, _ = self.carregar(ped(1, 24))
        self.assertEqual(len(ls), 1)

    def test_verificado_antigo_nao_entra_e_de_hoje_entra(self):
        ls, _ = self.carregar(ped(1, 24, data="2026-11-04"), ped(2, 24, data="2026-11-06"))
        self.assertEqual([l["pedido"] for l in ls], ["S2"])

    def test_feito_de_ontem_nao_volta(self):
        self.carregar(ped(1, 6))
        ontem = AGORA - timedelta(days=1)
        self.pedidos = {"S1": ped(1, 24)}
        s.linhas(ontem)
        ls, _ = s.linhas(AGORA)
        self.assertEqual(ls, [])

    def test_cancelado_nao_entra(self):
        self.assertEqual(self.carregar(ped(1, 12))[0], [])

    def test_detalhe_so_uma_vez(self):
        self.carregar(ped(1, 6)); self.carregar(ped(1, 6))
        self.assertEqual(self.chamadas.count("/pedidos/vendas/1"), 1)
        self.assertEqual(self.chamadas.count("/canais-venda"), 1)

    def test_modalidade_desconhecida_avisa_e_nao_conta(self):
        ls, erros = self.carregar(ped(5, 6))
        self.assertEqual(ls, [])
        self.assertIn("Outra Coisa", erros["shopee_modalidade"])

    def test_bling_fora_nao_derruba(self):
        with mock.patch.object(s.bling_checkout, "_pedidos", side_effect=RuntimeError("401")):
            ls, erros = s.linhas(AGORA)
        self.assertEqual(ls, [])
        self.assertIn("401", erros["shopee"])


if __name__ == "__main__":
    unittest.main()
