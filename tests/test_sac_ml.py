"""SAC: classificação do desfecho de contestações (sem rede). Estruturas copiadas de casos reais de 16/10/2026."""
import unittest

import ml_api


def med(txt, quando="2026-10-06T13:57:00.000-04:00"):
    return {"sender_role": "mediator", "message": txt, "date_created": quando, "attachments": []}


def refund(status, quando="2026-10-03T10:34:48.000-04:00", valor=119.69):
    return {"amount": valor, "date_created": quando,
            "metadata": {"coverage": {"id": 1}, "status_detail": status}}


class Desfecho(unittest.TestCase):
    def test_segundo_estorno_bpp_covered_e_reversao_no_pedido(self):
        col = {"refunds": [refund("bpp_refunded"), refund("bpp_covered", "2026-10-06T13:57:53.000-04:00")]}
        r = ml_api.sac_desfecho(col, [med("Olá")])
        self.assertEqual((r["desfecho"], r["confianca"]), ("REVERTIDO_NO_PEDIDO", "confirmado"))
        self.assertEqual(r["quando"], "2026-10-06T13:57:53.000-04:00")

    def test_estorno_unico_com_recusa_do_mediador(self):
        col = {"refunds": [refund("bpp_refunded")]}
        m = med("<p>Revisamos o seu caso e não poderemos ressarci-lo nesta ocasião porque a devolução cumpre</p>")
        self.assertEqual(ml_api.sac_desfecho(col, [m])["desfecho"], "RECUSADO")

    def test_recusa_com_impacto_na_reputacao(self):
        m = med("o comprador reafirma o problema que teve e, por esse motivo, essa reclamação impactará no cálculo")
        self.assertEqual(ml_api.sac_desfecho({"refunds": [refund("bpp_refunded")]}, [m])["desfecho"], "RECUSADO")

    def test_numero_da_operacao_no_texto_do_mediador(self):
        m = med("você vai receber o reembolso de R$ 41,41 correspondente a este item. "
                "buscando pelo número de operação{ 144443221493. Estamos à disposição")
        r = ml_api.sac_desfecho({"refunds": [refund("bpp_refunded")]}, [m])
        self.assertEqual((r["desfecho"], r["operacao"], r["valor"]), ("REVERTIDO_REEMBOLSO", 144443221493, 41.41))

    def test_credito_no_mesmo_horario_e_provavel(self):
        m = med("Revisamos e identificamos que você será reembolsado.", "2026-07-23T14:23:00.000-04:00")
        cred = [{"id": 99, "data": "2026-07-23T14:05:05.000-04:00", "valor": 32.9, "motivo": "reclamação"}]
        r = ml_api.sac_desfecho({"refunds": [refund("bpp_refunded")]}, [m], cred, [189.9, 32.9])
        self.assertEqual((r["desfecho"], r["confianca"], r["operacao"]), ("REVERTIDO_REEMBOLSO", "provável", 99))

    def test_credito_de_outro_valor_ou_hora_nao_vale(self):
        m = med("Revisamos o caso.", "2026-07-23T14:23:00.000-04:00")
        longe = [{"id": 1, "data": "2026-07-22T08:34:00.000-04:00", "valor": 32.9, "motivo": "r"}]
        outro = [{"id": 2, "data": "2026-07-23T14:05:00.000-04:00", "valor": 10.0, "motivo": "r"}]
        for cred in (longe, outro):
            self.assertEqual(ml_api.sac_desfecho({"refunds": []}, [m], cred, [32.9])["desfecho"], "SEM_DESFECHO")

    def test_so_texto_de_reembolso_nao_e_confirmado(self):
        m = med("Por isso, reembolsamos o valor correspondente na sua conta Mercado Pago.")
        r = ml_api.sac_desfecho({"refunds": [refund("bpp_refunded")]}, [m])
        self.assertEqual((r["desfecho"], r["confianca"]), ("REVERTIDO_NAO_CONFIRMADO", "só texto"))

    def test_mensagem_codificada_nao_decide(self):
        cod = med("LM7N1BcCI1iEJ1AmlRq8WtyjVOaHW/lpoS8a7Yh1d9um7zVnvc7N6loI/r3WojHzVm5z03eQVb+DqfKopUqqbIsXhaTydLearQj8kCN" * 3)
        self.assertEqual(ml_api.sac_texto(cod["message"]), "")
        self.assertEqual(ml_api.sac_desfecho({"refunds": [refund("bpp_refunded")]}, [cod])["desfecho"], "SEM_DESFECHO")

    def test_sem_colecao_nao_quebra(self):
        self.assertEqual(ml_api.sac_desfecho(None, [])["desfecho"], "SEM_DESFECHO")


class Textos(unittest.TestCase):
    def test_prazo_escrito_pelo_mediador(self):
        self.assertEqual(ml_api.sac_prazo_texto([med("Fico aguardando o seu retorno até o dia 15/07 . Caso não")]), "15/07")
        self.assertIsNone(ml_api.sac_prazo_texto([med("sem data")]))

    def test_etiqueta_da_devolucao(self):
        self.assertEqual(ml_api.sac_custo_etiqueta([med("O custo da etiqueta de devolução de R$11,36 será coberto por você.")]), 11.36)
        self.assertEqual(ml_api.sac_custo_etiqueta([med("O custo da etiqueta de devolução é de R$ 1.025,00 e fica por sua conta")]), 1025.0)
        self.assertIsNone(ml_api.sac_custo_etiqueta([med("frete de devolução é grátis")]))

    def test_minutos(self):
        self.assertEqual(ml_api._minutos("2026-07-23T14:23:00.000-04:00", "2026-07-23T14:05:00.000-04:00"), 18)


if __name__ == "__main__":
    unittest.main()
