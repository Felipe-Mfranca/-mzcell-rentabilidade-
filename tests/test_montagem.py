"""Testes do módulo de montagem de envios Full (leitor do PDF, etapas, TV e produtividade). Sem rede."""
import os
import tempfile
import unittest

from marketplaces.montagem import armazenamento as arm
from marketplaces.montagem import leitor

TEXTO_1 = """Frete #80429611
Produtos do envio: 1 | Total de unidades: 390
Lista de produtos e instruções de preparação
Código ML: AVHV86313 Código universal:
7894701444279 SKU: TORN-BANC-CAT1
Torneira Gourmet Cozinha Mesa Bancada Pia Flexível
Preta Abs Aquattro Preto Fosco
Etiquetagem
obrigatória
PRODUTO UNIDADES IDENTIFIÇÃO INSTRUÇÕES DE PREPARAÇÃO
390"""

TEXTO_2 = """Frete #111
Produtos do envio: 2 | Total de unidades: 50
Código ML: AAA111 Código universal:
7890000000001 SKU: SKU-A
Produto A
Etiquetagem
obrigatória
30
Código ML: BBB222 Código universal:
7890000000002 SKU: SKU-B
Produto B
Etiquetagem
obrigatória
20"""


class TestLeitor(unittest.TestCase):
    def test_um_produto(self):
        r = leitor.ler_texto(TEXTO_1)
        self.assertEqual(r["frete"], "80429611")
        self.assertTrue(r["confere"])
        p = r["produtos"][0]
        self.assertEqual((p["codigo_ml"], p["ean"], p["sku"], p["unidades"]),
                         ("AVHV86313", "7894701444279", "TORN-BANC-CAT1", 390))
        self.assertIn("Torneira Gourmet", p["titulo"])

    def test_varios_produtos(self):
        r = leitor.ler_texto(TEXTO_2)
        self.assertEqual([(p["sku"], p["unidades"]) for p in r["produtos"]], [("SKU-A", 30), ("SKU-B", 20)])
        self.assertTrue(r["confere"])

    def test_soma_nao_bate_avisa(self):
        r = leitor.ler_texto(TEXTO_2.replace("Total de unidades: 50", "Total de unidades: 99"))
        self.assertFalse(r["confere"])
        self.assertTrue(r["avisos"])

    def test_layout_estranho_nao_quebra(self):
        r = leitor.ler_texto("texto qualquer")
        self.assertFalse(r["confere"])
        self.assertEqual(r["produtos"], [])


class TestArmazenamento(unittest.TestCase):
    def setUp(self):
        self._orig = arm.CAMINHO
        self._dir = tempfile.TemporaryDirectory()
        arm.CAMINHO = os.path.join(self._dir.name, "montagem_envios.json")
        self.prods = [{"sku": "SKU-A", "unidades": 30, "estoquista": "Ana", "embalador": "Beto"},
                      {"sku": "SKU-B", "unidades": 20, "estoquista": "Caio", "embalador": "Beto"}]

    def tearDown(self):
        arm.CAMINHO = self._orig
        self._dir.cleanup()

    def _criar(self, canal="meli", numero="1"):
        return arm.criar(numero, "meli01", "2026-11-04", "2026-11-04T09:00", self.prods, "Lider", canal)

    def test_canais_nao_colidem(self):
        self._criar("meli", "123")
        self._criar("amazon", "123")
        with self.assertRaises(arm.ErroMontagem):
            self._criar("meli", "123")
        self.assertEqual(len(arm.listar()), 2)

    def test_canal_invalido(self):
        with self.assertRaises(arm.ErroMontagem):
            self._criar("outro")

    def test_ok_exige_t_e_e(self):
        e = self._criar()
        with self.assertRaises(arm.ErroMontagem) as c:
            arm.marcar(e["chave"], 0, "OK", "Ana", "estoquista")
        self.assertEqual(c.exception.status, 409)
        arm.marcar(e["chave"], 0, "T", "Ana", "estoquista")
        arm.marcar(e["chave"], 0, "E", "Ana", "estoquista")
        arm.marcar(e["chave"], 0, "OK", "Ana", "estoquista")

    def test_so_estoquista_do_produto_ou_lider(self):
        e = self._criar()
        with self.assertRaises(arm.ErroMontagem) as c:
            arm.marcar(e["chave"], 0, "T", "Caio", "estoquista")   # produto 0 é da Ana
        self.assertEqual(c.exception.status, 403)
        with self.assertRaises(arm.ErroMontagem):
            arm.marcar(e["chave"], 0, "T", "Beto", "embalador")    # embalador não marca
        arm.marcar(e["chave"], 0, "T", "Chefe", "lider")           # líder pode

    def test_conclusao_e_desfazer(self):
        e = self._criar()
        k = e["chave"]
        for i, quem in ((0, "Ana"), (1, "Caio")):
            for t in ("T", "E", "OK"):
                env = arm.marcar(k, i, t, quem, "estoquista")
        self.assertIsNotNone(env["concluido_em"])
        env = arm.desmarcar(k, 1, "T")                              # tirar T derruba o OK e reabre o envio
        self.assertIsNone(env["concluido_em"])
        self.assertNotIn("OK", env["produtos"][1]["etapas"])

    def test_resumo_e_tv_sem_produtividade(self):
        e = self._criar()
        k = e["chave"]
        for t in ("T", "E", "OK"):
            arm.marcar(k, 0, t, "Ana", "estoquista")
        env = arm.listar()[0]
        self.assertEqual(arm.resumo(env)["unidades_prontas"], 30)
        tv = arm.para_tv(env)
        self.assertEqual(tv["produtos"][0]["etapas"], {"T": True, "E": True, "OK": True})
        self.assertNotIn("criado_por", tv)
        self.assertNotIn("por", str(tv["produtos"][0]["etapas"]))

    def test_produtividade(self):
        e = self._criar()
        k = e["chave"]
        for t in ("T", "E", "OK"):
            arm.marcar(k, 0, t, "Ana", "estoquista")
        p = arm.produtividade()["pessoas"]
        self.assertEqual(p[0]["pessoa"], "Ana")
        self.assertEqual(p[0]["marcas"], 3)


if __name__ == "__main__":
    unittest.main()
