"""Classificação do aviso de rebot (sem rede)."""
import unittest

import rebot_visual as v


def op(**kw):
    base = dict(conta="meli01", mlb="MLB1", titulo="x", tipo="SMART", nome="n", atual_preco=100.0,
                atual_rebot=0.0, preco=100.0, rebot=5.0, ganho_un=5.0, delta_preco=0.0,
                un_30d=50, empate=False, estoque=None, fim="", impacto_mes=250.0)
    base.update(kw)
    return base


class SoPreco(unittest.TestCase):
    def test_rebot_zero_para_zero_com_preco_maior_nao_e_rebot(self):
        # MLB7652934580: R$727,24 -> R$766,00, rebot 0 -> 0
        self.assertTrue(v.so_preco(op(atual_preco=727.24, preco=766.0, rebot=0, delta_preco=38.76, ganho_un=34.3)))

    def test_rebot_que_some_com_preco_maior_nao_e_rebot(self):
        self.assertTrue(v.so_preco(op(atual_rebot=42.08, rebot=0, delta_preco=72.53)))

    def test_rebot_novo_com_preco_menor_e_rebot(self):
        self.assertFalse(v.so_preco(op(atual_rebot=0, rebot=3.91, delta_preco=-3.39)))

    def test_rebot_maior_mesmo_com_preco_maior_e_rebot(self):
        self.assertFalse(v.so_preco(op(atual_rebot=1.0, rebot=4.0, delta_preco=2.0)))


class Classificar(unittest.TestCase):
    def test_recomendado(self):
        self.assertEqual(v.classificar(op())[0], "OK")

    def test_aumento_de_preco(self):
        self.assertEqual(v.classificar(op(rebot=0, delta_preco=10))[0], "PRECO")

    def test_rebot_com_exigencia_de_estoque_e_avaliar(self):
        o = op(tipo="LIGHTNING", estoque={"min": 5, "max": 100}, atual_rebot=0, rebot=2.0)
        self.assertEqual(v.classificar(o)[0], "ATENC")

    def test_sem_venda_e_baixa(self):
        self.assertEqual(v.classificar(op(un_30d=0, impacto_mes=0))[0], "BAIXA")

    def test_ganho_irrelevante_e_baixa(self):
        self.assertEqual(v.classificar(op(ganho_un=0.03, impacto_mes=3.0))[0], "BAIXA")

    def test_potencial_nao_conta_aumento_de_preco(self):
        novas = [op(), op(mlb="MLB2", rebot=0, delta_preco=10, impacto_mes=999)]
        self.assertNotIn("999", v.texto(novas, {}, 0, "x", ["meli01"]).split("potencial")[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main()
