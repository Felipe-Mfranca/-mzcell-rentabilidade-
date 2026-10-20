"""Perfis e módulos de acesso: regras puras, sem arquivo de usuários real."""
import unittest

import auth_service


class Perfis(unittest.TestCase):
    def test_operador_so_ve_e_anota(self):
        self.assertTrue(auth_service.tem_permissao("operador", "ver"))
        self.assertTrue(auth_service.tem_permissao("operador", "anotacoes"))
        self.assertFalse(auth_service.tem_permissao("operador", "custos"))
        self.assertFalse(auth_service.tem_permissao("operador", "sync"))

    def test_master_faz_tudo_e_viewer_so_ve(self):
        self.assertTrue(auth_service.tem_permissao("master", "custos"))
        self.assertFalse(auth_service.tem_permissao("viewer", "sync"))


if __name__ == "__main__":
    unittest.main()
