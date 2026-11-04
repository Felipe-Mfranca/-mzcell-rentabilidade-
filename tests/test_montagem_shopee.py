"""Leitura do PDF 'Shopee Picking List' (FBS) — sem rede."""
import unittest

from marketplaces.montagem import leitor

TEXTO = (
    "Informação de Inbound\nShopee Picking List - Shopee Fulfillment\n"
    "Data de Inbound 2026-10-27 08:00 (GMT-03) INBRFRC12610140354ID de Envio (ASN ID)\n"
    "Método de Entrega Pickup\nNotas Total 400\nNo. SKU do\nQnt.\nAprovada\n"
    "controlps4-preto 29794313954_4\n35602243913\nControle Bluetooth Sem Fio\nPreto Ps4 Pc\n"
    "Item without\nGTIN,79003\n60948017\n400"
)


class TestLeitorShopee(unittest.TestCase):
    def test_le_asn_data_metodo_e_produto(self):
        r = leitor.ler_texto(TEXTO)
        self.assertEqual(r["canal"], "shopee")
        self.assertEqual(r["frete"], "INBRFRC12610140354")
        self.assertEqual(r["coleta"], "2026-10-27")
        self.assertEqual(r["metodo"], "Pickup")
        self.assertEqual([(p["sku"], p["unidades"]) for p in r["produtos"]], [("controlps4-preto", 400)])
        self.assertTrue(r["confere"])

    def test_total_diferente_gera_aviso(self):
        r = leitor.ler_texto(TEXTO.replace("Total 400", "Total 500"))
        self.assertFalse(r["confere"])
        self.assertTrue(r["avisos"])


if __name__ == "__main__":
    unittest.main()
