# Testes do fatiamento de tabela de itens (Passo 8) — detecção automática de
# cabeçalho (sem depender do texto exato de um edital específico) e escolha
# automática de qual arquivo, entre vários enviados, tem a tabela de itens.

from app.extracao.extrator import Bloco, DocumentoExtraido
from app.extracao.tabela_itens import fatiar_por_item, localizar_documento_com_tabela


def _documento(nome_arquivo: str, texto_pagina: str, pagina: int = 1) -> DocumentoExtraido:
    bloco = Bloco(pagina=pagina, localizador=f"página {pagina}", texto=texto_pagina)
    return DocumentoExtraido(
        nome_arquivo=nome_arquivo,
        tipo="pdf",
        num_paginas=pagina,
        blocos=[bloco],
        alertas=[],
    )


def test_fatiar_por_item_reconhece_cabecalho_com_redacao_diferente():
    # Redação diferente do "ITEM DESCRICAO DO PRODUTO" original — prova que
    # a detecção não está presa a um texto literal de um edital específico.
    texto = (
        "ITEM ESPECIFICACAO DO OBJETO UNIDADE QTD\n"
        "1 CADEIRA DE RODAS DOBRAVEL. UND 5\n"
        "2 MULETA AXILAR PAR. UND 20\n"
    )
    itens = fatiar_por_item(_documento("anexo.pdf", texto))
    assert [item.numero for item in itens] == [1, 2]
    assert "CADEIRA DE RODAS" in itens[0].texto
    assert "MULETA AXILAR" in itens[1].texto


def test_fatiar_por_item_documento_sem_tabela_devolve_lista_vazia():
    texto = (
        "CLAUSULA 1. DO OBJETO. O presente termo de referência tem por "
        "objeto a aquisição de materiais diversos, conforme especificações "
        "em anexo, sem nenhuma tabela de itens nesta página."
    )
    assert fatiar_por_item(_documento("edital.pdf", texto)) == []


def test_localizar_documento_com_tabela_escolhe_o_de_mais_itens():
    # "edital.pdf" tem só o cabeçalho aparecendo de raspão em prosa (não é
    # tabela de verdade) — no máximo bate 1 item por acidente. "anexo.pdf"
    # é a tabela de itens de verdade, com several itens sequenciais.
    texto_falso_positivo = (
        "O edital exige que o ITEM tenha DESCRICAO clara e QUANTIDADE "
        "compatível com a demanda, conforme detalhado no Anexo I. "
        "1 Isso aqui não é uma tabela de item de verdade."
    )
    texto_tabela_real = (
        "ITEM DESCRICAO DO PRODUTO UNIDADE QUANTIDADE\n"
        "1 TERMOMETRO DIGITAL. UND 10\n"
        "2 SERINGA DESCARTAVEL. UND 50\n"
        "3 LUVA DE PROCEDIMENTO. CX 100\n"
    )
    documento_falso = _documento("edital.pdf", texto_falso_positivo)
    documento_real = _documento("anexo.pdf", texto_tabela_real)

    resultado = localizar_documento_com_tabela([documento_falso, documento_real])

    assert resultado is not None
    documento_escolhido, itens = resultado
    assert documento_escolhido.nome_arquivo == "anexo.pdf"
    assert [item.numero for item in itens] == [1, 2, 3]


def test_localizar_documento_com_tabela_nenhum_documento_tem_tabela():
    texto = "Termo de referência sem nenhuma tabela de itens nesta página."
    resultado = localizar_documento_com_tabela([_documento("edital.pdf", texto)])
    assert resultado is None
