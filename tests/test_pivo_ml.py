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
        self.assertIn("O", ids(pivo_ml.avaliar(BOM, "d" * 149, PROMO, "active"), False))
        r = pivo_ml.avaliar(BOM, "d" * 3500, PROMO, "active")
        self.assertIn("O", ids(r, True))
        self.assertIn("longa", next(c for c in r["checks"] if c["id"] == "O")["detalhe"])

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
        self.assertEqual((r["passa"], r["pontos"], r["total"], r["atencao"]), (True, 6, 6, []))
        r = pivo_ml.avaliar(dict(BOM, video_id=None), "d" * 10, [], None)
        self.assertFalse(r["passa"])
        self.assertEqual(len(r["atencao"]), 4)   # clip não confirmado, descrição, promoção, ADS


class Cache(unittest.TestCase):
    def test_marcar_clip_recalcula_sem_chamar_o_ml(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cache, manual = os.path.join(d.name, "c.json"), os.path.join(d.name, "m.json")
        entrada = {"item": dict(BOM, video_id=None), "descricao_n": 200, "promocoes": PROMO, "ads": "active"}
        base = pivo_ml.avaliar(entrada["item"], "x" * 200, PROMO, "active")
        base["_entrada"] = entrada
        json.dump({"meli01": {"MLB1": base, "_meta": {"gerado_em": "x"}}}, open(cache, "w"))
        with mock.patch.object(pivo_ml, "CACHE_PATH", cache), mock.patch.object(pivo_ml, "MANUAL_PATH", manual):
            self.assertFalse(pivo_ml.resultado("meli01")["itens"]["MLB1"]["passa"])
            novo = pivo_ml.marcar_clip("meli01", "MLB1", True)
            self.assertTrue(novo["passa"])
            self.assertTrue(pivo_ml.resultado("meli01")["itens"]["MLB1"]["passa"])
            pivo_ml.marcar_clip("meli01", "MLB1", None)
            self.assertFalse(pivo_ml.resultado("meli01")["itens"]["MLB1"]["passa"])
            self.assertEqual(pivo_ml.resultado("meli01")["fora_do_pivo"], 1)


if __name__ == "__main__":
    unittest.main()
