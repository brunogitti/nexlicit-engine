# Testes do validador de trecho (app/validacao/validador.py). Puro
# processamento de texto — nenhum teste aqui chama a API do Gemini nem toca
# rede.
#
# O teste de ouro usa um trecho real de edital (Câmara Municipal de Lins,
# Passo 3) como fixture. Esse dado real fica fora do Git, na pasta
# editais-reais/ (ver .gitignore) — se o arquivo não existir nesta máquina, o
# teste é pulado automaticamente em vez de falhar.

import json
from pathlib import Path

import pytest

from app.extracao.extrator import Bloco, DocumentoExtraido
from app.validacao.validador import validar_exigencias

CAMINHO_FIXTURE_OURO = (
    Path(__file__).resolve().parent.parent / "editais-reais" / "teste_ouro_camara_lins.json"
)


def _documento_sintetico(
    textos_por_pagina: list[str], nome_arquivo: str = "edital_teste.pdf"
) -> DocumentoExtraido:
    blocos = [
        Bloco(pagina=indice + 1, localizador=f"página {indice + 1}", texto=texto)
        for indice, texto in enumerate(textos_por_pagina)
    ]
    return DocumentoExtraido(
        nome_arquivo=nome_arquivo,
        tipo="pdf",
        num_paginas=len(textos_por_pagina),
        blocos=blocos,
        alertas=[],
    )


def _exigencia_sintetica(trecho, **overrides) -> dict:
    base = {
        "categoria": "habilitacao_fiscal_social_trabalhista",
        "descricao": "descrição de teste",
        "base_legal": None,
        "trecho": trecho,
        "obrigatorio_para": "todos",
    }
    base.update(overrides)
    return base


@pytest.mark.skipif(
    not CAMINHO_FIXTURE_OURO.exists(),
    reason=(
        "fixture do teste de ouro (dado real, fora do git) não está "
        "presente nesta máquina — ver editais-reais/ no .gitignore"
    ),
)
def test_teste_de_ouro_camara_lins_todas_localizadas():
    with open(CAMINHO_FIXTURE_OURO, encoding="utf-8") as arquivo:
        dados = json.load(arquivo)

    bloco = Bloco(
        pagina=dados["documento"]["pagina"],
        localizador=dados["documento"]["localizador"],
        texto=dados["documento"]["texto"],
    )
    documento = DocumentoExtraido(
        nome_arquivo=dados["documento"]["nome_arquivo"],
        tipo="pdf",
        num_paginas=dados["documento"]["pagina"],
        blocos=[bloco],
        alertas=[],
    )

    resultado = validar_exigencias(dados["exigencias_extraidas"], [documento])

    assert len(resultado) == 5
    for exigencia in resultado:
        assert exigencia["confianca"] == "localizado", (
            f"esperava 'localizado' para: {exigencia['descricao']}"
        )
        assert exigencia["pagina"] == dados["documento"]["pagina"]
        assert exigencia["localizador"] == dados["documento"]["localizador"]
        assert exigencia["arquivo_origem"] == dados["documento"]["nome_arquivo"]
        assert exigencia["ocorrencias_encontradas"] == 1
        # Confere que categoria/descricao/base_legal/obrigatorio_para do
        # Passo 3 não foram alterados pelo validador.
        assert exigencia["categoria"] in (
            "habilitacao_juridica",
            "habilitacao_fiscal_social_trabalhista",
        )


def test_trecho_alterado_cai_como_inferido():
    documento = _documento_sintetico(
        ["Cláusula 5. O licitante deve apresentar Certidão Negativa de Débitos Federais."]
    )
    # Trocado "Negativa" por "Positiva" de propósito: mesmo tamanho, conteúdo
    # diferente — não pode bater.
    exigencia = _exigencia_sintetica(
        "O licitante deve apresentar Certidão Positiva de Débitos Federais."
    )

    resultado = validar_exigencias([exigencia], [documento])

    assert resultado[0]["confianca"] == "inferido"
    assert resultado[0]["pagina"] is None
    assert resultado[0]["localizador"] is None
    assert resultado[0]["arquivo_origem"] is None
    assert resultado[0]["ocorrencias_encontradas"] == 0


def test_trecho_vazio_cai_inferido_sem_tentar_buscar():
    documento = _documento_sintetico(
        ["Texto qualquer da página, com tamanho suficiente para não ser o problema aqui."]
    )

    for trecho_vazio in ("", None, "   "):
        exigencia = _exigencia_sintetica(trecho_vazio)
        resultado = validar_exigencias([exigencia], [documento])
        assert resultado[0]["confianca"] == "inferido"
        assert resultado[0]["ocorrencias_encontradas"] == 0


def test_trecho_curto_demais_cai_inferido_mesmo_existindo_no_texto():
    # "CNPJ" existe literalmente no texto abaixo, mas tem só 4 caracteres —
    # abaixo do TAMANHO_MINIMO_TRECHO, não deve nem tentar buscar.
    documento = _documento_sintetico(
        ["Este texto contém a palavra CNPJ dentro dele, só para efeito de teste automatizado."]
    )
    exigencia = _exigencia_sintetica("CNPJ")

    resultado = validar_exigencias([exigencia], [documento])

    assert resultado[0]["confianca"] == "inferido"


def test_trecho_repetido_sinaliza_multiplas_ocorrencias():
    texto_repetido = (
        "A empresa deve apresentar certidão negativa de débitos junto ao INSS "
        "antes da assinatura do contrato."
    )
    documento = _documento_sintetico([texto_repetido, texto_repetido])  # 2 páginas iguais
    exigencia = _exigencia_sintetica(
        "certidão negativa de débitos junto ao INSS antes da assinatura do contrato."
    )

    resultado = validar_exigencias([exigencia], [documento])

    assert resultado[0]["confianca"] == "localizado"
    assert resultado[0]["ocorrencias_encontradas"] == 2
    # Registra a primeira ocorrência (página 1), mesmo havendo mais de uma.
    assert resultado[0]["pagina"] == 1


def test_trecho_dividido_entre_duas_paginas_cai_localizado_com_intervalo():
    # Bloco 1 termina em "...conforme o artigo", bloco 2 começa em "66 da
    # lei;" — o trecho completo só existe juntando os dois.
    bloco_1 = "Cláusula 9. A empresa deve cumprir suas obrigações fiscais conforme o artigo"
    bloco_2 = "66 da lei; sob pena de inabilitação sumária no certame licitatório."
    documento = _documento_sintetico([bloco_1, bloco_2])
    exigencia = _exigencia_sintetica("conforme o artigo 66 da lei;")

    resultado = validar_exigencias([exigencia], [documento])

    assert resultado[0]["confianca"] == "localizado"
    assert resultado[0]["cruzou_pagina"] is True
    assert resultado[0]["pagina"] == "1-2"
    assert resultado[0]["localizador"] == "página 1-2"
    assert resultado[0]["ocorrencias_encontradas"] == 1
    assert resultado[0]["arquivo_origem"] == "edital_teste.pdf"


def test_normaliza_espacos_multiplos_e_quebras_de_linha():
    documento = _documento_sintetico(
        ["Prova de   regularidade\npara com o  FGTS,\tconforme a lei vigente sobre o tema."]
    )
    exigencia = _exigencia_sintetica(
        "Prova de regularidade para com o FGTS, conforme a lei vigente sobre o tema."
    )

    resultado = validar_exigencias([exigencia], [documento])

    assert resultado[0]["confianca"] == "localizado"


def test_normaliza_aspas_curvas_e_travessao():
    documento = _documento_sintetico(
        ['A empresa deve apresentar a “Certidão Negativa” – documento obrigatório para habilitação.']
    )
    exigencia = _exigencia_sintetica(
        'A empresa deve apresentar a "Certidão Negativa" - documento obrigatório para habilitação.'
    )

    resultado = validar_exigencias([exigencia], [documento])

    assert resultado[0]["confianca"] == "localizado"


def test_nao_reavalia_outros_campos_da_exigencia():
    texto = "Texto suficientemente longo para não cair no limite mínimo de caracteres deste teste."
    documento = _documento_sintetico([texto])
    exigencia = _exigencia_sintetica(
        texto,
        categoria="habilitacao_juridica",
        descricao="descrição original, não deve mudar",
        base_legal="art. 66",
        obrigatorio_para="vencedor",
    )

    resultado = validar_exigencias([exigencia], [documento])

    assert resultado[0]["categoria"] == "habilitacao_juridica"
    assert resultado[0]["descricao"] == "descrição original, não deve mudar"
    assert resultado[0]["base_legal"] == "art. 66"
    assert resultado[0]["obrigatorio_para"] == "vencedor"


def test_busca_em_varios_documentos_registra_arquivo_certo():
    documento_1 = _documento_sintetico(
        ["Texto do primeiro arquivo, sem relação nenhuma com a exigência buscada aqui."],
        nome_arquivo="edital.pdf",
    )
    documento_2 = _documento_sintetico(
        ["Prova de regularidade perante o FGTS deve ser apresentada na fase de habilitação."],
        nome_arquivo="anexo_termo_referencia.pdf",
    )
    exigencia = _exigencia_sintetica(
        "Prova de regularidade perante o FGTS deve ser apresentada na fase de habilitação."
    )

    resultado = validar_exigencias([exigencia], [documento_1, documento_2])

    assert resultado[0]["confianca"] == "localizado"
    assert resultado[0]["arquivo_origem"] == "anexo_termo_referencia.pdf"
