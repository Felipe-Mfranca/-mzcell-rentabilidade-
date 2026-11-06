"""Trava dos módulos em construção (modulos_liberados.py). Sem rede, sem credencial."""
import json
import os
import unittest
from unittest import mock

import modulos_liberados

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestModulosLiberados(unittest.TestCase):
    def test_em_construcao_saem_fechados(self):
        # sem a abertura por máquina: o servidor abre módulos em modulos_locais.json e o teste não deve depender dele
        with mock.patch.object(modulos_liberados, "_locais", return_value={}):
            for nome in ("balanco", "compras", "radar"):
                self.assertFalse(modulos_liberados.liberado(nome), nome)

    def test_modulo_desconhecido_fica_fechado(self):
        self.assertFalse(modulos_liberados.liberado("nao_existe"))

    def test_lista_online_so_tem_caminhos_validos_e_existentes(self):
        with open(os.path.join(RAIZ, "arquivos_online.json"), encoding="utf-8") as f:
            lista = json.load(f)
        self.assertIn("modulos_liberados.py", lista)
        for a in lista:
            if a.endswith(".json"):  # legado: o app ignora o que não for .py/.html
                continue
            self.assertTrue(os.path.exists(os.path.join(RAIZ, a)), a)


if __name__ == "__main__":
    unittest.main()
