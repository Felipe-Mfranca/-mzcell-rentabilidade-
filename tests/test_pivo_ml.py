"""Análise PIVO dos anúncios do ML: regras puras, sem rede."""
import json
import os
import tempfile
import unittest
from unittest import mock

import pivo_ml

TITULO60 = "Microfone Sem Fio Duplo Uhf Profissional Preto Recarregavel!"   # 60 caracteres
IMG_OK = {"max_size": "1200x1200"}
BOM = {"title": TITULO60, "pictures": [IMG_OK] * 5, "video_id": "abc"}
PROMO = [{"type": "SMART", "status": "started", "name": "Impulsione suas vendas"}]


def ids(r, estado):
    return [c["id"] for c in r["checks"] if c["ok"] is estado]


class Regras(unittest.TestCase):
    def test_titulo_tem_60_caracteres(self):
        self.assertEqual(len(TITULO60), 60)
        self.assertIn("P", ids(pivo_ml.avaliar(BOM, "d" * 200, PROMO, "active"), True))
        curto = dict(BOM, title=TITULO60[:59])
        self.assertIn("P", ids(pivo_ml.avaliar(curto, "d" * 200, PROMO, "active"), False))

    def test_imagens_contam_so_1200_ou_maior(self):
        pics = [{"max_size": "1200x1200"}] * 4 + [{"max_size": "1085x1200"}, {"max_size": "1024x1024"}, {"max_size": "2000x1500"}]
        r = pivo_ml.avaliar(dict(BOM, pictures=pics), "d" * 200, PROMO, "active")
        self.assertIn("I", ids(r, True))                  # 4 + a de 2000x1500 = 5
        r = pivo_ml.avaliar(dict(BOM, pictures=pics[:4] + pics[4:6]), "d" * 200, PROMO, "active")
        self.assertIn("I", ids(r, False))

    def test_clip_tres_estados(self):
        sem = dict(BOM, video_id=None)
        self.assertIn("V", ids(pivo_ml.avaliar(sem, "d" * 200, PROMO, "active"), None))          # não confirmado
        self.assertIn("V", ids(pivo_ml.avaliar(sem, "d" * 200, PROMO, "active", True), True))
        self.assertIn("V", ids(pivo_ml.avaliar(sem, "d" * 200, PROMO, "active", False), False))
        self.assertIn("V", ids(pivo_ml.avaliar(BOM, "d" * 200, PROMO, "active"), True))

    def test_descricao_minima_e_aviso_de_longa(self):
        self.assertIn("D", ids(pivo_ml.avaliar(BOM, "d" * 149, PROMO, "active"), False))
        r = pivo_ml.avaliar(BOM, "d" * 3500, PROMO, "active")
        self.assertIn("D", ids(r, True))
        self.assertIn("longa", next(c for c in r["checks"] if c["id"] == "D")["detalhe"])

    def test_promocao_ignora_banco_e_candidata(self):
        so_banco = [{"type": "BANK", "status": "started", "name": "PIX"}, {"type": "DEAL", "status": "candidate", "name": "x"}]
        self.assertIn("PROMO", ids(pivo_ml.avaliar(BOM, "d" * 200, so_banco, "active"), False))
        pendente = [{"type": "SELLER_CAMPAIGN", "status": "pending", "name": "20 OUT a 19 NOV"}]
        self.assertIn("PROMO", ids(pivo_ml.avaliar(BOM, "d" * 200, pendente, "active"), True))

    def test_ads_precisa_estar_ativo(self):
        for st in (None, "paused"):
            self.assertIn("ADS", ids(pivo_ml.avaliar(BOM, "d" * 200, PROMO, st), False))

    def test_passa_so_com_tudo_ok_e_lista_atencao(self):
        r = pivo_ml.avaliar(BOM, "d" * 200, PROMO, "active")
        self.assertEqual((r["passa"], r["pontos"], r["total"], r["atencao"]), (True, 4, 4, []))
        r = pivo_ml.avaliar(dict(BOM, video_id=None), "d" * 10, [], None)
        self.assertFalse(r["passa"])
        self.assertEqual(len(r["atencao"]), 4)   # clip não confirmado, descrição, promoção, ADS


class Grupos(unittest.TestCase):
    def grupos(self, r):
        return {g["id"]: g["ok"] for g in r["grupos"]}

    def test_quatro_grupos_p_i_v_o(self):
        r = pivo_ml.avaliar(BOM, "d" * 200, PROMO, "active")
        self.assertEqual(self.grupos(r), {"P": True, "I": True, "V": True, "O": True})

    def test_p_exige_titulo_e_descricao(self):
        self.assertFalse(self.grupos(pivo_ml.avaliar(dict(BOM, title="curto"), "d" * 200, PROMO, "active"))["P"])
        self.assertFalse(self.grupos(pivo_ml.avaliar(BOM, "d" * 10, PROMO, "active"))["P"])

    def test_o_exige_promocao_e_ads(self):
        self.assertFalse(self.grupos(pivo_ml.avaliar(BOM, "d" * 200, PROMO, None))["O"])
        self.assertFalse(self.grupos(pivo_ml.avaliar(BOM, "d" * 200, [], "active"))["O"])
        r = pivo_ml.avaliar(BOM, "d" * 200, [], None)
        self.assertEqual([p["id"] for p in next(g for g in r["grupos"] if g["id"] == "O")["partes"]], ["PROMO", "ADS"])

    def test_v_desconhecido_fica_sem_cor(self):
        self.assertIsNone(self.grupos(pivo_ml.avaliar(dict(BOM, video_id=None), "d" * 200, PROMO, "active"))["V"])

    def test_resultado_antigo_sem_grupos_e_refeito(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cache = os.path.join(d.name, "c.json")
        antigo = {"checks": [], "pontos": 3, "total": 6, "passa": False, "atencao": [],
                  "_entrada": {"item": BOM, "descricao_n": 200, "promocoes": PROMO, "ads": "active"}}
        json.dump({"meli01": {"MLB1": antigo, "_meta": {"gerado_em": "x"}}}, open(cache, "w"))
        with mock.patch.object(pivo_ml, "CACHE_PATH", cache), mock.patch.object(pivo_ml, "MANUAL_PATH", os.path.join(d.name, "m.json")):
            r = pivo_ml.resultado("meli01")["itens"]["MLB1"]
        self.assertTrue(r["passa"])
        self.assertEqual(r["total"], 4)


class Cache(unittest.TestCase):
    def test_marcar_clip_recalcula_sem_chamar_o_ml(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cache, manual = os.path.join(d.name, "c.json"), os.path.join(d.name, "m.json")
        entrada = {"item": dict(BOM, video_id=None), "descricao_n": 200, "promocoes": PROMO, "ads": "active"}
        base = pivo_ml.avaliar(entrada["item"], "x" * 200, PROMO, "active")
        base["_entrada"] = entrada
        json.dump({"meli01": {"MLB1": base, "_meta": {"gerado_em": "x"}}}, open(cache, "w"))
        remoto = cache + ".remoto"   # isola do arquivo compartilhado real (pivo_compartilhado.json)
        with mock.patch.object(pivo_ml, "CACHE_PATH", cache), mock.patch.object(pivo_ml, "MANUAL_PATH", manual), mock.patch.object(pivo_ml, "REMOTO_PATH", remoto):
            self.assertFalse(pivo_ml.resultado("meli01")["itens"]["MLB1"]["passa"])
            novo = pivo_ml.marcar_clip("meli01", "MLB1", True)
            self.assertTrue(novo["passa"])
            self.assertTrue(pivo_ml.resultado("meli01")["itens"]["MLB1"]["passa"])
            pivo_ml.marcar_clip("meli01", "MLB1", None)
            self.assertFalse(pivo_ml.resultado("meli01")["itens"]["MLB1"]["passa"])
            self.assertEqual(pivo_ml.resultado("meli01")["fora_do_pivo"], 1)


class ContaNoPivo(unittest.TestCase):
    def test_so_ativo_ou_pausado_apenas_por_estoque(self):
        casos = [("active", [], True), ("paused", ["out_of_stock"], True), ("paused", ["paused_by_seller"], False),
                 ("paused", ["out_of_stock", "paused_by_seller"], False), ("closed", ["deleted"], False),
                 ("closed", [], False), ("under_review", ["forbidden"], False), (None, [], True)]
        for status, sub, espera in casos:
            self.assertEqual(pivo_ml.considerar({"status": status, "sub_status": sub}), espera, (status, sub))

    def test_resultado_tira_da_conta_e_marcar_clip_mantem(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cache, manual = os.path.join(d.name, "c.json"), os.path.join(d.name, "m.json")

        def anuncio(status, sub):
            e = {"item": dict(BOM, video_id=None, status=status, sub_status=sub), "descricao_n": 200, "promocoes": PROMO, "ads": "active"}
            return dict(pivo_ml.avaliar(e["item"], "x" * 200, PROMO, "active"), _entrada=e)
        with open(cache, "w") as f:
            json.dump({"meli01": {"MLB1": anuncio("paused", ["out_of_stock"]), "MLB2": anuncio("paused", ["paused_by_seller"]),
                                  "MLB3": anuncio("closed", ["deleted"]), "_meta": {"gerado_em": "x"}}}, f)
        with mock.patch.object(pivo_ml, "CACHE_PATH", cache), mock.patch.object(pivo_ml, "MANUAL_PATH", manual), mock.patch.object(pivo_ml, "REMOTO_PATH", cache + ".r"):
            r = pivo_ml.resultado("meli01")
            self.assertEqual((r["total"], r["fora_do_pivo"], r["nao_considerados"]), (1, 1, 2))
            self.assertEqual(len(r["itens"]), 3)
            self.assertFalse(pivo_ml.marcar_clip("meli01", "MLB2", True)["considerar"])
            self.assertEqual(pivo_ml.resultado("meli01")["fora_do_pivo"], 1)


class AgendaDiaria(unittest.TestCase):
    AGORA = __import__("datetime").datetime(2026, 11, 6, 9, 0)

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.cache = os.path.join(d.name, "c.json")
        for alvo, nome, v in ((pivo_ml, "CACHE_PATH", self.cache), (pivo_ml, "_estado", {}), (pivo_ml, "_tentativas", {})):
            p = mock.patch.object(alvo, nome, v); p.start(); self.addCleanup(p.stop)

    def gravar(self, **datas):
        with open(self.cache, "w", encoding="utf-8") as f:
            json.dump({c: {"_meta": {"gerado_em": v}} for c, v in datas.items()}, f)

    def test_uma_conta_por_vez_ate_todas_terem_a_de_hoje(self):
        self.gravar(meli01="2026-11-06T06:10:00", meli02="2026-10-22T10:00:00")
        self.assertEqual(pivo_ml.proxima_conta(self.AGORA), "meli02")
        self.gravar(meli01="2026-11-06T06:10:00", meli02="2026-11-06T06:30:00")
        self.assertEqual(pivo_ml.proxima_conta(self.AGORA), "meli03")
        self.gravar(meli01="2026-11-06T06:10:00", meli02="2026-11-06T06:30:00", meli03="2026-11-06T07:00:00")
        self.assertIsNone(pivo_ml.proxima_conta(self.AGORA))

    def test_nao_comeca_antes_da_hora_nem_com_outra_rodando(self):
        self.assertIsNone(pivo_ml.proxima_conta(self.AGORA.replace(hour=5)))
        pivo_ml._estado["meli02"] = {"rodando": True}
        self.assertIsNone(pivo_ml.proxima_conta(self.AGORA))

    def test_falha_nao_insiste_o_dia_todo(self):
        self.gravar(meli02="2026-11-06T06:30:00", meli03="2026-11-06T07:00:00")
        pivo_ml._tentativas[("meli01", "2026-11-06")] = pivo_ml.AUTO_TENTATIVAS
        self.assertIsNone(pivo_ml.proxima_conta(self.AGORA))

    def test_leitura_que_falha_nao_apaga_o_ultimo_resultado(self):
        self.gravar(meli01="2026-11-05T06:10:00")
        ml = mock.Mock()
        ml.get.return_value = (200, [])
        pivo_ml._estado["meli01"] = {"rodando": True}
        pivo_ml._analisar("meli01", ml, ["MLB1"])
        with open(self.cache, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["meli01"]["_meta"]["gerado_em"], "2026-11-05T06:10:00")
        self.assertEqual(pivo_ml._estado["meli01"]["etapa"], "falhou")
        self.assertFalse(pivo_ml._estado["meli01"]["rodando"])


if __name__ == "__main__":
    unittest.main()
