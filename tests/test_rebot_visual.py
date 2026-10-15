"""Classificação do aviso de rebot (sem rede)."""
import unittest

import rebot_visual as v


def op(**kw):
    base = dict(conta="meli01", mlb="MLB1", titulo="x", tipo="SMART", nome="n", atual_preco=100.0,
                atual_rebot=0.0, preco=100.0, rebot=5.0, ganho_un=5.0, delta_preco=0.0,
                un_30d=50, empate=False, estoque=None, fim="", impacto_mes=250.0)
    base.update(kw)
    return base


class Classificar(unittest.TestCase):
    def test_recomendado(self):
        self.assertEqual(v.classificar(op())[0], "OK")

    def test_rebot_com_exigencia_de_estoque_e_avaliar(self):
        o = op(tipo="LIGHTNING", estoque={"min": 5, "max": 100}, atual_rebot=0, rebot=2.0)
        self.assertEqual(v.classificar(o)[0], "ATENC")

    def test_sem_venda_e_baixa(self):
        self.assertEqual(v.classificar(op(un_30d=0, impacto_mes=0))[0], "BAIXA")

    def test_ganho_irrelevante_e_baixa(self):
        self.assertEqual(v.classificar(op(ganho_un=0.03, impacto_mes=3.0))[0], "BAIXA")


if __name__ == "__main__":
    unittest.main()
