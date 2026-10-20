"""Aviso de ruptura no Full: regras puras, sem rede."""
import unittest
from datetime import date

import full_alerta as f


class Regra(unittest.TestCase):
    def test_zerado(self):
        self.assertEqual(f.classificar(0, 3.0), ("zerado", 0.0))
        self.assertEqual(f.classificar(0, 0, ativo=True), ("zerado", 0.0))

    def test_zerado_e_parado_nao_alerta(self):
        self.assertEqual(f.classificar(0, 0, ativo=False, vendeu_30d=False), (None, None))
        self.assertEqual(f.classificar(0, 0, ativo=False, vendeu_30d=True), ("zerado", 0.0))

    def test_cobertura_ate_10_dias_e_atencao_ate_5_e_critico(self):
        self.assertEqual(f.classificar(50, 10), ("critico", 5.0))      # 5 dias
        self.assertEqual(f.classificar(51, 10), ("atencao", 5.1))
        self.assertEqual(f.classificar(100, 10), ("atencao", 10.0))    # exatamente 10 dias ainda não cobre MAIS de 10
        self.assertEqual(f.classificar(101, 10), (None, 10.1))

    def test_anuncio_pausado_nao_alerta_ruptura(self):
        self.assertEqual(f.classificar(3, 4.4, ativo=False), (None, None))

    def test_sem_vendas_com_saldo_nao_alerta(self):
        self.assertEqual(f.classificar(30, 0), (None, None))


class Media(unittest.TestCase):
    def test_media_dos_7_dias_fechados(self):
        dias = {f"2026-10-{d:02d}": {"unidades": 7} for d in range(12, 19)}     # 12..18
        self.assertEqual(f.media_diaria(dias, date(2026, 10, 18)), 7.0)
        self.assertEqual(f.media_diaria(dias, date(2026, 10, 19)), 6.0)         # o dia 19 não tem venda: 6 dias com 7 / 7
        self.assertEqual(f.media_diaria({}, date(2026, 10, 18)), 0.0)


class Calculo(unittest.TestCase):
    class ML:
        def get(self, path, **kw):
            if path == "/items":
                itens = {"MLB1": {"inventory_id": "INV1", "shipping": {"logistic_type": "fulfillment"}, "status": "active"},
                         "MLB2": {"inventory_id": "INV1", "shipping": {"logistic_type": "fulfillment"}, "status": "paused"},
                         "MLB3": {"inventory_id": None, "shipping": {"logistic_type": "xd_drop_off"}, "status": "active"}}
                return 200, [{"code": 200, "body": {"id": k, **v}} for k, v in itens.items()]
            if path.startswith("/inventories/INV1/stock/fulfillment"):
                return 200, {"available_quantity": 30, "not_available_quantity": 4,
                             "not_available_detail": [{"status": "transfer", "quantity": 4}], "total": 34}
            return 404, None

    def test_soma_vendas_do_inventario_e_ignora_quem_nao_e_full(self):
        un = {f"2026-10-{d:02d}": {"unidades": 2} for d in range(12, 19)}
        produtos = {"MLB1": {"conta": "meli01", "dias": un}, "MLB2": {"conta": "meli01", "dias": un}, "MLB3": {"conta": "meli01", "dias": un}}
        r = f.calcular("meli01", self.ML(), produtos, date(2026, 10, 18))
        self.assertEqual(set(r), {"MLB1", "MLB2"})
        self.assertEqual(r["MLB1"]["saldo"], 34)            # disponível 30 + transferência 4
        self.assertEqual(r["MLB1"]["media_7d"], 4.0)        # 2 anúncios x 2 un/dia
        self.assertEqual((r["MLB1"]["nivel"], r["MLB1"]["dias_cobertura"]), ("atencao", 8.5))


if __name__ == "__main__":
    unittest.main()
