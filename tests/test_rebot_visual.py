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

    def test_sem_venda_com_ganho_e_recomendado_para_alavancar(self):
        nivel, motivos = v.classificar(op(un_30d=0, impacto_mes=0, ganho_un=5.0))
        self.assertEqual(nivel, "OK")
        self.assertIn("alavancar", motivos[0])

    def test_sem_venda_com_preco_subindo_e_avaliar(self):
        self.assertEqual(v.classificar(op(un_30d=0, impacto_mes=0, ganho_un=5.0, delta_preco=3.0))[0], "ATENC")

    def test_sem_venda_e_ganho_irrelevante_e_baixa(self):
        self.assertEqual(v.classificar(op(un_30d=0, impacto_mes=0, ganho_un=0.03))[0], "BAIXA")

    def test_ganho_irrelevante_e_baixa(self):
        self.assertEqual(v.classificar(op(ganho_un=0.03, impacto_mes=3.0))[0], "BAIXA")


class Repetidas(unittest.TestCase):
    def test_sugestao_ja_avisada_continua_no_relatorio(self):
        ops = [op(mlb="MLB1", repetida=True), op(mlb="MLB2", repetida=False)]
        txt = v.texto(ops, {}, 0, "19/10 08:00", ["meli01"])
        html = v.html(ops, {}, 0, "19/10 08:00", ["meli01"])
        for m in ("MLB1", "MLB2"):
            self.assertIn(m, txt); self.assertIn(m, html)
        self.assertIn("já avisada", txt); self.assertIn("1 nova(s), 1 já avisada(s)", txt)

    def test_montar_nao_suprime_repetidas(self):
        import rebot_agenda as a
        assunto, texto, html = a.montar([op(repetida=True)], {}, "19/10 08:00")
        self.assertIn("1 oportunidade", assunto)
        self.assertIn("MLB1", texto)


if __name__ == "__main__":
    unittest.main()
