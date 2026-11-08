"""Carga do servidor às 22h e às 6h: horários, etapas isoladas e rebot salvo. Sem rede."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import carga_servidor as cs


class TestProximaCarga(unittest.TestCase):
    def test_22h_e_6h_de_brasilia(self):
        casos = [(datetime(2026, 11, 6, 21, 10), datetime(2026, 11, 7, 1, 0)),    # 18h10 BRT -> 22h
                 (datetime(2026, 11, 7, 1, 0), datetime(2026, 11, 7, 9, 0)),      # 22h em ponto -> 6h
                 (datetime(2026, 11, 7, 8, 59), datetime(2026, 11, 7, 9, 0)),     # 5h59 -> 6h
                 (datetime(2026, 11, 7, 9, 30), datetime(2026, 11, 8, 1, 0))]     # 6h30 -> 22h
        for agora, espera in casos:
            self.assertEqual(cs.proxima_carga(agora), espera, agora)

    def test_maquina_comum_so_6h(self):
        self.assertEqual(cs.proxima_carga(datetime(2026, 11, 7, 9, 30), (6,)), datetime(2026, 11, 8, 9, 0))


class TestEtapas(unittest.TestCase):
    def test_falha_de_uma_etapa_nao_para_as_outras(self):
        feitas, logs = [], []

        async def pivo(c):
            feitas.append(("pivo", c))
            if c == "meli02":
                raise RuntimeError("401 no ML")

        async def ok(c):
            feitas.append(("outra", c))

        with mock.patch.object(cs, "_pivo", pivo), mock.patch.object(cs, "_rebot", ok), mock.patch.object(cs, "_amazon", ok):
            r = asyncio.run(cs.rodar_extras(logs.append))
        self.assertEqual(len(feitas), 8)
        self.assertEqual(r["pivo meli02"], "RuntimeError: 401 no ML")
        self.assertEqual(r["amazon amazon02"], "ok")
        self.assertEqual(len(logs), 8)

    def test_espera_acaba_e_estoura_o_limite(self):
        asyncio.run(cs._esperar(lambda: False, 30))

        async def sem_espera(_seg):
            return None

        with mock.patch.object(cs.asyncio, "sleep", sem_espera), self.assertRaises(TimeoutError):
            asyncio.run(cs._esperar(lambda: True, 30))


class TestEmDia(unittest.TestCase):
    def test_regra_da_carga_em_dia(self):
        ok6 = {"ok": True, "inicio": "2026-11-09T06:00:00"}
        ok22 = {"ok": True, "inicio": "2026-11-08T22:00:00"}
        self.assertTrue(cs.em_dia(ok6, datetime(2026, 11, 9, 15, 0)))
        self.assertTrue(cs.em_dia(ok22, datetime(2026, 11, 9, 6, 10)))       # 6h ainda rodando: vale a das 22h
        self.assertFalse(cs.em_dia(ok22, datetime(2026, 11, 9, 9, 0)))        # a das 6h não rodou
        self.assertFalse(cs.em_dia({"ok": False, "inicio": "2026-11-09T06:00:00"}, datetime(2026, 11, 9, 9, 0)))
        self.assertTrue(cs.em_dia({"ok": True, "inicio": "2026-11-09T10:15:00"}, datetime(2026, 11, 9, 11, 0)))   # carga manual
        self.assertFalse(cs.em_dia(None, datetime(2026, 11, 9, 9, 0)))

    def test_carga_completa_grava_o_estado_e_nao_roda_duas_vezes(self):
        async def meli(log):
            return {"meli meli01": "ok"}

        async def extras(log):
            return {"pivo meli01": "ok", "amazon amazon02": "ErroAmazon: 503"}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cs, "ESTADO_PATH", os.path.join(d, "e.json")), \
                mock.patch.object(cs, "sync_meli", meli), mock.patch.object(cs, "rodar_extras", extras), \
                mock.patch("modulos_liberados.liberado", return_value=True):
            u = asyncio.run(cs.executar_completa(lambda m: None, "manual por ana"))
            self.assertEqual((u["ok"], list(u["erros"]), u["etapas"]), (False, ["amazon amazon02"], 3))
            e = cs.estado()
            self.assertEqual((e["ativo"], e["rodando"], e["em_dia"], e["ultima"]["origem"]), (True, False, False, "manual por ana"))
            cs._rodando["desde"] = "x"
            self.assertEqual(asyncio.run(cs.executar_completa(lambda m: None, "outra")), {})
            cs._rodando["desde"] = None
        with mock.patch("modulos_liberados.liberado", return_value=False):
            self.assertEqual(cs.estado(), {"ativo": False})


class TestRebotSalvo(unittest.TestCase):
    def test_grava_por_conta_sem_apagar_as_outras(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cs, "REBOT_PATH", os.path.join(d, "r.json")):
            self.assertIsNone(cs.rebot_salvo("meli01"))
            cs._rebot_gravar("meli01", {"total": 3})
            cs._rebot_gravar("meli02", {"total": 1})
            self.assertEqual(cs.rebot_salvo("meli01"), {"total": 3})
            self.assertEqual(cs.rebot_salvo("meli02"), {"total": 1})


if __name__ == "__main__":
    unittest.main()
