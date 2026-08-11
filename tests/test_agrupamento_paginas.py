# Testes das funções puras de agrupamento usadas na tela de checklist
# (app/rotas/paginas.py) — sem TestClient, sem banco: só a lógica de separar
# exigências em "grupos" (hipóteses alternativas, Passo 9/Mudança 3) e
# "avulsas" (cada uma com seu próprio card, como sempre), e a de agrupar
# requisitos por item (Passo 8) por categoria em vez de por item (Mudança 1).

from app.rotas.paginas import (
    LIMITE_BLOCOS_REQUISITOS_ITEM_ABERTO,
    _agrupar_hipoteses,
    _agrupar_por_categoria,
    _agrupar_requisitos_por_categoria,
    _chave_dedup_trecho,
)


def _exigencia(id, **overrides) -> dict:
    base = {
        "id": id,
        "categoria": "habilitacao_juridica",
        "descricao": "descrição de teste",
        "status_check": "pendente",
        "confianca": "localizado",
        "grupo_hipoteses": None,
    }
    base.update(overrides)
    return base


def test_agrupar_hipoteses_junta_alternativas_com_mesmo_titulo():
    exigencias = [
        _exigencia(1, descricao="Registro comercial", grupo_hipoteses="Documento constitutivo"),
        _exigencia(2, descricao="Contrato social", grupo_hipoteses="Documento constitutivo"),
        _exigencia(3, descricao="CNPJ"),  # avulsa, sem grupo
    ]

    grupos, avulsas = _agrupar_hipoteses(exigencias)

    assert len(grupos) == 1
    assert grupos[0]["titulo"] == "Documento constitutivo"
    assert grupos[0]["exigencia_ids"] == [1, 2]
    assert [h["descricao"] for h in grupos[0]["hipoteses"]] == ["Registro comercial", "Contrato social"]

    assert len(avulsas) == 1
    assert avulsas[0]["descricao"] == "CNPJ"


def test_agrupar_hipoteses_grupo_com_hipotese_unica_vira_avulsa():
    # A IA marcou grupo_hipoteses, mas não achou nenhuma outra exigência com
    # o mesmo título nesta categoria — sem par, não é "alternativa" de nada,
    # então cai como card avulso normal (não faz sentido um card de
    # "escolha uma hipótese" com hipótese só).
    exigencias = [_exigencia(1, descricao="Registro comercial", grupo_hipoteses="Documento constitutivo")]

    grupos, avulsas = _agrupar_hipoteses(exigencias)

    assert grupos == []
    assert len(avulsas) == 1
    assert avulsas[0]["id"] == 1


def test_agrupar_hipoteses_todas_ok_so_quando_todas_as_hipoteses_estao_ok():
    exigencias = [
        _exigencia(1, grupo_hipoteses="Documento constitutivo", status_check="ok"),
        _exigencia(2, grupo_hipoteses="Documento constitutivo", status_check="pendente"),
    ]
    grupos, _ = _agrupar_hipoteses(exigencias)
    assert grupos[0]["todas_ok"] is False

    exigencias[1]["status_check"] = "ok"
    grupos, _ = _agrupar_hipoteses(exigencias)
    assert grupos[0]["todas_ok"] is True


def test_agrupar_por_categoria_expoe_grupos_e_exigencias_avulsas():
    exigencias = [
        _exigencia(1, categoria="habilitacao_juridica", descricao="Registro comercial", grupo_hipoteses="Doc"),
        _exigencia(2, categoria="habilitacao_juridica", descricao="Contrato social", grupo_hipoteses="Doc"),
        _exigencia(3, categoria="habilitacao_juridica", descricao="CNPJ"),
        _exigencia(4, categoria="declaracoes_exigidas", descricao="Declaração X"),
    ]

    resultado = _agrupar_por_categoria(exigencias)

    juridica = next(g for g in resultado if g["chave"] == "habilitacao_juridica")
    assert len(juridica["grupos"]) == 1
    assert len(juridica["exigencias"]) == 1

    declaracoes = next(g for g in resultado if g["chave"] == "declaracoes_exigidas")
    assert declaracoes["grupos"] == []
    assert len(declaracoes["exigencias"]) == 1

    # Categoria sem nenhuma exigência (ex.: qualificacao_tecnica aqui) não
    # aparece — comportamento de sempre, não deve mudar.
    assert not any(g["chave"] == "qualificacao_tecnica" for g in resultado)


def _requisito_item(numero_item, **overrides) -> dict:
    base = {
        "numero_item": numero_item,
        "categoria": "amostra",
        "gatilho": "APRESENTAR AMOSTRA",
        "trecho": "APRESENTAR AMOSTRA.",
        "localizador": "página 74",
    }
    base.update(overrides)
    return base


def test_chave_dedup_trecho_ignora_pontuacao_decorativa():
    # Aspas/asterisco de ruído de extração em volta de texto por lo resto
    # igual — devem virar a mesma chave.
    assert _chave_dedup_trecho("'APRESENTAR AMOSTRA*.") == _chave_dedup_trecho("'APRESENTAR AMOSTRA'.")
    assert _chave_dedup_trecho("APRESENTAR AMOSTRA.") == _chave_dedup_trecho("apresentar amostra")


def test_chave_dedup_trecho_preserva_numeros_com_ponto_decimal():
    # Ponto DENTRO do número (separador de milhar/decimal) não é pontuação
    # decorativa — "2.000" e "2000" são textos DIFERENTES, não podem colidir.
    assert _chave_dedup_trecho("GARANTIA DE 2.000 HORAS.") != _chave_dedup_trecho("GARANTIA DE 2000 HORAS.")


def test_agrupar_requisitos_por_categoria_junta_itens_com_mesmo_texto():
    requisitos = [
        _requisito_item(1, categoria="amostra", trecho="APRESENTAR AMOSTRA."),
        _requisito_item(57, categoria="amostra", trecho="APRESENTAR AMOSTRA."),
        _requisito_item(58, categoria="amostra", trecho="'APRESENTAR AMOSTRA'."),  # ruído decorativo, mesmo texto
    ]

    resultado = _agrupar_requisitos_por_categoria(requisitos)

    assert len(resultado) == 1
    bloco = resultado[0]
    assert bloco["categoria"] == "amostra"
    assert bloco["rotulo"] == "Amostra"
    assert len(bloco["grupos_trecho"]) == 1
    assert bloco["grupos_trecho"][0]["itens"] == [1, 57, 58]


def test_agrupar_requisitos_por_categoria_mantem_textos_diferentes_separados():
    requisitos = [
        _requisito_item(1, categoria="registro_sanitario", trecho="Registro na ANVISA."),
        _requisito_item(2, categoria="registro_sanitario", trecho="Registro no Ministério da Saúde, validade mínima de 12 meses."),
    ]

    resultado = _agrupar_requisitos_por_categoria(requisitos)

    bloco = resultado[0]
    assert len(bloco["grupos_trecho"]) == 2
    assert bloco["grupos_trecho"][0]["itens"] == [1]
    assert bloco["grupos_trecho"][1]["itens"] == [2]


def test_agrupar_requisitos_por_categoria_mesmo_item_nao_duplica_na_lista():
    # Item 280 tipo Ouroeste: duas linhas (gatilhos MANUAL e CATÁLOGO) com o
    # MESMO trecho — o item só deve aparecer UMA vez na lista de itens do
    # grupo, mesmo vindo de duas linhas diferentes na origem.
    requisitos = [
        _requisito_item(280, categoria="documentacao_produto", gatilho="MANUAL", trecho="Manual e catálogo em português."),
        _requisito_item(280, categoria="documentacao_produto", gatilho="CATALOGO", trecho="Manual e catálogo em português."),
    ]

    resultado = _agrupar_requisitos_por_categoria(requisitos)

    assert resultado[0]["grupos_trecho"][0]["itens"] == [280]


def test_agrupar_requisitos_por_categoria_ordena_texto_mais_compartilhado_primeiro():
    requisitos = [
        _requisito_item(1, categoria="amostra", trecho="Texto raro."),
        _requisito_item(2, categoria="amostra", trecho="APRESENTAR AMOSTRA."),
        _requisito_item(3, categoria="amostra", trecho="APRESENTAR AMOSTRA."),
        _requisito_item(4, categoria="amostra", trecho="APRESENTAR AMOSTRA."),
    ]

    resultado = _agrupar_requisitos_por_categoria(requisitos)

    grupos_trecho = resultado[0]["grupos_trecho"]
    assert grupos_trecho[0]["trecho"] == "APRESENTAR AMOSTRA."  # 3 itens, vem primeiro
    assert grupos_trecho[1]["trecho"] == "Texto raro."  # 1 item, vem depois


def test_agrupar_requisitos_por_categoria_total_itens_e_uniao_nao_soma():
    # Item 280 tipo Ouroeste: MESMO item aparecendo em 2 grupos_trecho
    # diferentes da MESMA categoria (dois textos distintos, mas ambos sobre
    # o item 280) — o contador "N itens" do cabeçalho recolhível (Mudança 5)
    # tem que contar o item UMA vez, não duas, senão o resumo do card mente.
    requisitos = [
        _requisito_item(280, categoria="documentacao_produto", trecho="Manual em português."),
        _requisito_item(280, categoria="documentacao_produto", trecho="Catálogo em português."),
        _requisito_item(281, categoria="documentacao_produto", trecho="Manual em português."),
    ]

    resultado = _agrupar_requisitos_por_categoria(requisitos)

    assert resultado[0]["total_itens"] == 2  # itens 280 e 281, não 3


def test_agrupar_requisitos_por_categoria_respeita_ordem_fixa_das_categorias():
    requisitos = [
        _requisito_item(1, categoria="garantia", trecho="Garantia de 12 meses."),
        _requisito_item(2, categoria="amostra", trecho="APRESENTAR AMOSTRA."),
    ]

    resultado = _agrupar_requisitos_por_categoria(requisitos)

    # amostra vem antes de garantia na ordem fixa (RÓTULOS_CATEGORIA_REQUISITO_ITEM),
    # mesmo garantia tendo aparecido primeiro na lista de entrada.
    assert [b["categoria"] for b in resultado] == ["amostra", "garantia"]


# ---------- Contador "X de Y conferidas" por categoria (Passo 9/Mudança 5) ----------


def test_agrupar_por_categoria_conta_avulsas_e_grupo_como_exigencias_reais():
    exigencias = [
        _exigencia(1, categoria="habilitacao_juridica", descricao="Registro comercial", grupo_hipoteses="Doc", status_check="ok"),
        _exigencia(2, categoria="habilitacao_juridica", descricao="Contrato social", grupo_hipoteses="Doc", status_check="ok"),
        _exigencia(3, categoria="habilitacao_juridica", descricao="CNPJ", status_check="ok"),
        _exigencia(4, categoria="habilitacao_juridica", descricao="FGTS", status_check="pendente"),
    ]

    resultado = _agrupar_por_categoria(exigencias)
    juridica = resultado[0]

    # total: 2 do grupo (conta as 2 linhas reais, não 1 card) + 2 avulsas = 4.
    assert juridica["total"] == 4
    # feitas: grupo inteiro ok (2) + CNPJ ok (1) = 3; FGTS pendente não conta.
    assert juridica["feitas"] == 3


def test_agrupar_por_categoria_grupo_parcialmente_ok_nao_conta_como_feito():
    exigencias = [
        _exigencia(1, categoria="habilitacao_juridica", grupo_hipoteses="Doc", status_check="ok"),
        _exigencia(2, categoria="habilitacao_juridica", grupo_hipoteses="Doc", status_check="pendente"),
    ]

    resultado = _agrupar_por_categoria(exigencias)

    assert resultado[0]["total"] == 2
    assert resultado[0]["feitas"] == 0  # só conta quando TODAS as hipóteses do grupo estão ok


# ---------- Limite de abertura por padrão (Passo 9/Mudança 5) ----------


def test_limite_blocos_requisitos_item_aberto_e_20():
    # Calibrado com dado real: Paulínia (16 blocos) abre; Ouroeste (43) e
    # Frutal (280) fecham — o limite fica no meio, sem caso de fronteira.
    assert LIMITE_BLOCOS_REQUISITOS_ITEM_ABERTO == 20
