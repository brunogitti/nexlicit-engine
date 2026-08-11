# Testes do extrator determinístico de exigências técnicas por item
# (Passo 8) — puro processamento de texto sobre uma tabela de itens
# sintética. Sem IA, sem rede, sem validador do Passo 4 (o trecho já vem
# direto do texto-fonte, por construção).

from app.extracao.exigencias_item import (
    carregar_palavras_chave,
    deduplicar_exigencias_item,
    extrair_exigencias_do_item,
)
from app.extracao.extrator import Bloco, DocumentoExtraido
from app.extracao.tabela_itens import fatiar_por_item

PALAVRAS_CHAVE_TESTE = {
    "registro_sanitario": ["ANVISA", "REGISTRO NO MS", "MINISTERIO DA SAUDE"],
    "metrologia": ["INMETRO"],
    "garantia": ["GARANTIA"],
    "certificacao": ["CERTIFICADO DE BOAS PRATICAS", "CERTIFICADO"],
    "documentacao_produto": ["MANUAL", "CATALOGO", "CATÁLOGO"],
}


def _documento_sintetico(texto_pagina: str) -> DocumentoExtraido:
    bloco = Bloco(pagina=1, localizador="página 1", texto=texto_pagina)
    return DocumentoExtraido(
        nome_arquivo="edital_teste.pdf",
        tipo="pdf",
        num_paginas=1,
        blocos=[bloco],
        alertas=[],
    )


def test_carregar_palavras_chave_reais_tem_as_categorias_esperadas():
    palavras_chave = carregar_palavras_chave()
    assert set(palavras_chave.keys()) == {
        "amostra",
        "registro_sanitario",
        "metrologia",
        "normas_tecnicas",
        "laudo",
        "documentacao_produto",
        "garantia",
        "certificacao",
        "assistencia_tecnica",
    }
    assert "ANVISA" in palavras_chave["registro_sanitario"]


def test_item_com_multiplas_categorias_na_mesma_descricao():
    texto_tabela = (
        "ITEM DESCRICAO DO PRODUTO/SERVICO UNIDADE QUANTIDADE\n"
        "1 TERMOMETRO DIGITAL INFRAVERMELHO COM REGISTRO NA ANVISA E SELO "
        "INMETRO. APRESENTAR MANUAL EM PORTUGUES E GARANTIA MINIMA DE 12 "
        "MESES. UND 10\n"
        "2 SERINGA DESCARTAVEL 10ML BICO LUER LOCK, CAIXA COM 100 UNIDADES. "
        "UND 50\n"
    )
    documento = _documento_sintetico(texto_tabela)
    itens = fatiar_por_item(documento)
    assert len(itens) == 2
    assert itens[0].numero == 1
    assert itens[1].numero == 2

    exigencias_item_1 = extrair_exigencias_do_item(itens[0], PALAVRAS_CHAVE_TESTE)
    categorias = {e["categoria"] for e in exigencias_item_1}

    # 3 categorias na mesma descrição (ANVISA, INMETRO, MANUAL+GARANTIA).
    assert categorias == {
        "registro_sanitario",
        "metrologia",
        "documentacao_produto",
        "garantia",
    }
    assert all(e["numero_item"] == 1 for e in exigencias_item_1)
    assert all(e["pagina"] == 1 for e in exigencias_item_1)
    assert all(e["localizador"] == "página 1" for e in exigencias_item_1)

    # Item 2 não tem nenhuma palavra-chave.
    exigencias_item_2 = extrair_exigencias_do_item(itens[1], PALAVRAS_CHAVE_TESTE)
    assert exigencias_item_2 == []


def test_certificado_boas_praticas_nao_duplica_com_certificado_generico():
    texto_tabela = (
        "ITEM DESCRICAO DO PRODUTO/SERVICO UNIDADE QUANTIDADE\n"
        "1 KIT DE TESTE RAPIDO. APRESENTAR CERTIFICADO DE BOAS PRATICAS DE "
        "FABRICACAO. UND 5\n"
    )
    documento = _documento_sintetico(texto_tabela)
    itens = fatiar_por_item(documento)
    exigencias = extrair_exigencias_do_item(itens[0], PALAVRAS_CHAVE_TESTE)

    achados_certificacao = [e for e in exigencias if e["categoria"] == "certificacao"]

    # "CERTIFICADO" é substring de "CERTIFICADO DE BOAS PRATICAS" — sem a
    # regra de sobreposição, isso bateria duas vezes na mesma ocorrência.
    assert len(achados_certificacao) == 1
    assert achados_certificacao[0]["gatilho"] == "CERTIFICADO DE BOAS PRATICAS"


def test_trecho_nao_corta_numero_com_ponto_como_separador_de_milhar():
    texto_tabela = (
        "ITEM DESCRICAO DO PRODUTO/SERVICO UNIDADE QUANTIDADE\n"
        "1 BALANCA DIGITAL CAPACIDADE ATE 2.000 KG. APRESENTAR GARANTIA "
        "MINIMA DE 12 MESES. UND 3\n"
    )
    documento = _documento_sintetico(texto_tabela)
    itens = fatiar_por_item(documento)
    exigencias = extrair_exigencias_do_item(itens[0], PALAVRAS_CHAVE_TESTE)

    achado_garantia = next(e for e in exigencias if e["categoria"] == "garantia")

    # O trecho começa na frase certa (depois do ponto de "...KG."), não no
    # início do item, e "2.000" não quebra a detecção de frase.
    assert achado_garantia["trecho"] == "APRESENTAR GARANTIA MINIMA DE 12 MESES."


def test_gatilho_e_trecho_preservam_acento_e_caixa_originais():
    texto_tabela = (
        "ITEM DESCRICAO DO PRODUTO/SERVICO UNIDADE QUANTIDADE\n"
        "1 Bomba de Infusão Volumétrica. Apresentar Catálogo e Manual em "
        "Português. UND 2\n"
    )
    documento = _documento_sintetico(texto_tabela)
    itens = fatiar_por_item(documento)
    exigencias = extrair_exigencias_do_item(itens[0], PALAVRAS_CHAVE_TESTE)

    gatilhos = {e["gatilho"] for e in exigencias if e["categoria"] == "documentacao_produto"}
    # A busca ignora acento/caixa, mas o gatilho reportado vem do texto
    # ORIGINAL (com acento e caixa reais, não tudo maiúsculo sem acento).
    assert "Catálogo" in gatilhos
    assert "Manual" in gatilhos


def test_deduplicar_exigencias_item_junta_achado_repetido_e_conta_ocorrencias():
    # Reproduz o caso do item 280 do edital de Ouroeste: a descrição inteira
    # (e portanto o mesmo achado de categoria) aparece duplicada no PDF de
    # origem — um erro real de exportação da planilha, não do nosso código.
    # A frase-gatilho vem precedida de um ponto final nas duas cópias (igual
    # ao caso real), pra o trecho extraído sair idêntico nas duas ocorrências.
    texto_tabela = (
        "ITEM DESCRICAO DO PRODUTO/SERVICO UNIDADE QUANTIDADE\n"
        "1 SERINGA HOSPITALAR DESCARTAVEL. FABRICADA COM REGISTRO NA ANVISA. "
        "SERINGA HOSPITALAR DESCARTAVEL. FABRICADA COM REGISTRO NA ANVISA. "
        "UND 10\n"
    )
    documento = _documento_sintetico(texto_tabela)
    itens = fatiar_por_item(documento)
    exigencias_brutas = extrair_exigencias_do_item(itens[0], PALAVRAS_CHAVE_TESTE)

    achados_brutos = [e for e in exigencias_brutas if e["categoria"] == "registro_sanitario"]
    assert len(achados_brutos) == 2

    dedup = deduplicar_exigencias_item(exigencias_brutas)
    achados_dedup = [e for e in dedup if e["categoria"] == "registro_sanitario"]
    assert len(achados_dedup) == 1
    assert achados_dedup[0]["ocorrencias_encontradas"] == 2


def test_deduplicar_exigencias_item_nao_funde_gatilhos_diferentes_na_mesma_frase():
    # Reproduz o caso real do item 280 na categoria documentacao_produto:
    # "MANUAL" e "CATALOGO" são gatilhos DIFERENTES que caem na mesma frase
    # (mesmo trecho extraído) — isso não é a mesma exigência duplicada no
    # documento, é coincidência de dois termos na mesma janela de texto.
    # Sem o gatilho na chave de agrupamento, isso fundia num registro só com
    # contagem inflada; com o gatilho na chave, viram DOIS registros.
    texto_tabela = (
        "ITEM DESCRICAO DO PRODUTO/SERVICO UNIDADE QUANTIDADE\n"
        "1 BOMBA DE INFUSAO VOLUMETRICA. APRESENTAR MANUAL E CATALOGO EM "
        "PORTUGUES. UND 5\n"
    )
    documento = _documento_sintetico(texto_tabela)
    itens = fatiar_por_item(documento)
    exigencias_brutas = extrair_exigencias_do_item(itens[0], PALAVRAS_CHAVE_TESTE)

    achados_brutos = [e for e in exigencias_brutas if e["categoria"] == "documentacao_produto"]
    assert len(achados_brutos) == 2
    assert achados_brutos[0]["trecho"] == achados_brutos[1]["trecho"]
    assert {e["gatilho"] for e in achados_brutos} == {"MANUAL", "CATALOGO"}

    dedup = deduplicar_exigencias_item(exigencias_brutas)
    achados_dedup = [e for e in dedup if e["categoria"] == "documentacao_produto"]

    # Dois registros separados, um por gatilho, cada um com
    # ocorrencias_encontradas=1 (não apareceram duplicados no documento).
    assert len(achados_dedup) == 2
    assert {e["gatilho"] for e in achados_dedup} == {"MANUAL", "CATALOGO"}
    assert all(e["ocorrencias_encontradas"] == 1 for e in achados_dedup)


def test_deduplicar_exigencias_item_nao_junta_achados_diferentes():
    texto_tabela = (
        "ITEM DESCRICAO DO PRODUTO/SERVICO UNIDADE QUANTIDADE\n"
        "1 TERMOMETRO COM REGISTRO NA ANVISA E SELO INMETRO. UND 10\n"
    )
    documento = _documento_sintetico(texto_tabela)
    itens = fatiar_por_item(documento)
    exigencias_brutas = extrair_exigencias_do_item(itens[0], PALAVRAS_CHAVE_TESTE)

    dedup = deduplicar_exigencias_item(exigencias_brutas)

    # ANVISA e INMETRO são achados diferentes (categorias diferentes) — não
    # devem ser juntados, e cada um continua com ocorrencias_encontradas=1.
    assert len(dedup) == len(exigencias_brutas)
    assert all(e["ocorrencias_encontradas"] == 1 for e in dedup)
