# Testes das páginas HTML (Passo 7) via TestClient. Mesmo padrão de
# isolamento de banco/uploads do test_rotas.py: monkeypatch em
# app.db.conexao.DATABASE_PATH e app.rotas.processos.UPLOAD_DIR, apontando
# pra dentro do tmp_path do pytest.
#
# Nenhum teste aqui chama a API real do Gemini.

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.repositorio import (
    atualizar_status_exigencia,
    criar_arquivo,
    criar_processo,
    salvar_exigencias,
    salvar_requisitos_item,
)
from app.ia.llm_client import _parsear_resposta
from app.validacao.validador import validar_exigencias
from app.extracao.extrator import Bloco, DocumentoExtraido

CAMINHO_FIXTURE_OURO = (
    Path(__file__).resolve().parent.parent / "editais-reais" / "teste_ouro_camara_lins.json"
)


@pytest.fixture
def cliente_teste(tmp_path, monkeypatch) -> TestClient:
    caminho_db = str(tmp_path / "teste.db")
    pasta_uploads = tmp_path / "uploads"
    pasta_uploads.mkdir()

    monkeypatch.setattr("app.db.conexao.DATABASE_PATH", caminho_db)
    monkeypatch.setattr("app.rotas.processos.UPLOAD_DIR", str(pasta_uploads))

    from app.main import app

    cliente = TestClient(app)
    cliente.caminho_db = caminho_db  # guarda pra testes que gravam direto no banco
    return cliente


def test_formulario_novo_processo_devolve_html_200(cliente_teste):
    resposta = cliente_teste.get("/processos/novo")

    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]
    assert "<form" in resposta.text
    assert 'name="nome"' in resposta.text
    assert 'name="arquivos"' in resposta.text
    assert 'type="file"' in resposta.text


def test_painel_principal_vazio_mostra_convite_em_vez_de_tabela_em_branco(cliente_teste):
    resposta = cliente_teste.get("/")

    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]
    assert "Nenhum processo analisado ainda" in resposta.text
    # Não é uma tabela/lista vazia sem contexto: tem um link de ação.
    assert 'href="/processos/novo"' in resposta.text


def test_painel_principal_com_dados_mostra_cards(cliente_teste):
    criar_processo(
        {"nome": "Dispensa 001/2026", "orgao": "Prefeitura Teste", "modalidade": "Dispensa"},
        caminho_banco=cliente_teste.caminho_db,
    )

    resposta = cliente_teste.get("/")

    assert resposta.status_code == 200
    assert "Dispensa 001/2026" in resposta.text
    assert "Prefeitura Teste" in resposta.text
    assert "Nenhum processo analisado ainda" not in resposta.text


def test_get_processos_continua_somente_json_sem_ramificacao_html(cliente_teste):
    criar_processo({"nome": "Dispensa 001/2026"}, caminho_banco=cliente_teste.caminho_db)

    resposta = cliente_teste.get("/processos", headers={"accept": "text/html"})

    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("application/json")
    assert isinstance(resposta.json(), list)


def test_checklist_processo_inexistente_devolve_404_com_pagina_html(cliente_teste):
    resposta = cliente_teste.get("/processos/999999/checklist")

    assert resposta.status_code == 404
    assert "text/html" in resposta.headers["content-type"]
    assert "não encontrado" in resposta.text.lower()


@pytest.mark.skipif(
    not CAMINHO_FIXTURE_OURO.exists(),
    reason=(
        "fixture do teste de ouro (dado real, fora do git) não está "
        "presente nesta máquina — ver editais-reais/ no .gitignore"
    ),
)
def test_checklist_teste_de_ouro_camara_lins_categorizado_e_com_selo_certo(cliente_teste):
    with open(CAMINHO_FIXTURE_OURO, encoding="utf-8") as arquivo:
        dados_fixture = json.load(arquivo)

    bloco = Bloco(
        pagina=dados_fixture["documento"]["pagina"],
        localizador=dados_fixture["documento"]["localizador"],
        texto=dados_fixture["documento"]["texto"],
    )
    documento = DocumentoExtraido(
        nome_arquivo=dados_fixture["documento"]["nome_arquivo"],
        tipo="pdf",
        num_paginas=dados_fixture["documento"]["pagina"],
        blocos=[bloco],
        alertas=[],
    )

    resposta_bruta = json.dumps(dados_fixture["resposta_ia_bruta_por_categoria"])
    exigencias_extraidas = _parsear_resposta(resposta_bruta)
    exigencias_validadas = validar_exigencias(exigencias_extraidas, [documento])

    caminho_db = cliente_teste.caminho_db
    processo_id = criar_processo(
        {"nome": "Dispensa 056/2026", "orgao": "Câmara Municipal de Lins"},
        caminho_banco=caminho_db,
    )
    criar_arquivo(
        processo_id,
        {
            "nome_arquivo": documento.nome_arquivo,
            "tipo": "pdf",
            "num_paginas": documento.num_paginas,
            "texto_extraido": bloco.texto,
        },
        caminho_banco=caminho_db,
    )
    salvar_exigencias(processo_id, exigencias_validadas, caminho_banco=caminho_db)

    resposta = cliente_teste.get(f"/processos/{processo_id}/checklist")
    html = resposta.text

    assert resposta.status_code == 200
    assert "Câmara Municipal de Lins" in html

    # As duas categorias reais aparecem, na ordem fixa da lei (Jurídica
    # antes de Fiscal/Social/Trabalhista), mesmo a exigência fiscal tendo
    # sido salva depois no banco.
    assert "Habilitação Jurídica" in html
    assert "Habilitação Fiscal, Social e Trabalhista" in html
    assert html.index("Habilitação Jurídica") < html.index("Habilitação Fiscal, Social e Trabalhista")

    # Categorias sem nenhuma exigência não aparecem (nunca mostra seção vazia).
    assert "Qualificação Técnica" not in html
    assert "Requisitos da Proposta" not in html

    # As 5 exigências reais bateram como "localizado" no Passo 4 — o selo
    # certo tem que aparecer 5 vezes, nenhum "inferido".
    assert html.count('class="selo selo-localizado"') == 5
    assert 'class="selo selo-inferido"' not in html

    # Trechos reais aparecem literalmente na página.
    assert "Fundo de Garantia por Tempo de Serviço (FGTS)" in html


# ---------- Cards recolhíveis por categoria (Passo 9/Mudança 5) ----------


def _requisito_item_sintetico(numero_item, categoria, trecho) -> dict:
    return {
        "numero_item": numero_item,
        "categoria": categoria,
        "gatilho": "APRESENTAR AMOSTRA",
        "trecho": trecho,
        "pagina": 1,
        "localizador": "página 1",
        "ocorrencias_encontradas": 1,
    }


def test_requisitos_por_item_abaixo_do_limite_comeca_aberto(cliente_teste):
    # Poucos blocos (bem abaixo de LIMITE_BLOCOS_REQUISITOS_ITEM_ABERTO) —
    # não faz sentido esconder uma seção pequena, começa aberta.
    processo_id = criar_processo({"nome": "Processo pequeno"}, caminho_banco=cliente_teste.caminho_db)
    requisitos = [
        _requisito_item_sintetico(n, "amostra", f"Texto único do item {n}.")
        for n in range(1, 6)  # 5 blocos, bem abaixo do limite
    ]
    salvar_requisitos_item(processo_id, requisitos, caminho_banco=cliente_teste.caminho_db)

    resposta = cliente_teste.get(f"/processos/{processo_id}/checklist")
    html = resposta.text

    inicio = html.index("card-requisito-categoria")
    fim_tag = html.index(">", inicio)
    # <details ... open> — confirma que o card abre por padrão.
    assert "open" in html[inicio:fim_tag]


def test_requisitos_por_item_acima_do_limite_comeca_fechado(cliente_teste):
    # Muitos blocos (acima do limite) — evita a rolagem enorme, começa
    # fechado; o usuário abre o que quiser conferir.
    from app.rotas.paginas import LIMITE_BLOCOS_REQUISITOS_ITEM_ABERTO

    processo_id = criar_processo({"nome": "Processo grande"}, caminho_banco=cliente_teste.caminho_db)
    requisitos = [
        _requisito_item_sintetico(n, "amostra", f"Texto único do item {n}.")
        for n in range(1, LIMITE_BLOCOS_REQUISITOS_ITEM_ABERTO + 5)
    ]
    salvar_requisitos_item(processo_id, requisitos, caminho_banco=cliente_teste.caminho_db)

    resposta = cliente_teste.get(f"/processos/{processo_id}/checklist")
    html = resposta.text

    inicio = html.index("card-requisito-categoria")
    fim_tag = html.index(">", inicio)
    assert "open" not in html[inicio:fim_tag]


def test_botao_expandir_recolher_tudo_aparece_quando_ha_conteudo(cliente_teste):
    processo_id = criar_processo({"nome": "Processo com conteúdo"}, caminho_banco=cliente_teste.caminho_db)
    salvar_requisitos_item(
        processo_id,
        [_requisito_item_sintetico(1, "amostra", "APRESENTAR AMOSTRA.")],
        caminho_banco=cliente_teste.caminho_db,
    )

    resposta = cliente_teste.get(f"/processos/{processo_id}/checklist")
    html = resposta.text

    assert "btn-expandir-tudo" in html
    assert "btn-recolher-tudo" in html


def test_botao_expandir_recolher_tudo_nao_aparece_sem_conteudo(cliente_teste):
    processo_id = criar_processo({"nome": "Processo vazio"}, caminho_banco=cliente_teste.caminho_db)

    resposta = cliente_teste.get(f"/processos/{processo_id}/checklist")
    html = resposta.text

    assert "btn-expandir-tudo" not in html
    assert "btn-recolher-tudo" not in html


# ---------- Feedback visual do checkbox (Passo 9/Mudança 2) ----------


def _exigencia_sintetica_paginas(**overrides) -> dict:
    base = {
        "categoria": "habilitacao_juridica",
        "descricao": "Contrato social",
        "base_legal": None,
        "trecho": "texto qualquer o suficiente para passar do limite mínimo",
        "obrigatorio_para": "todos",
        "confianca": "inferido",
    }
    base.update(overrides)
    return base


def test_card_conferido_ganha_classe_visual_so_quando_marcado(cliente_teste):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=cliente_teste.caminho_db)
    ids = salvar_exigencias(
        processo_id,
        [
            _exigencia_sintetica_paginas(descricao="Contrato social"),
            _exigencia_sintetica_paginas(descricao="CNPJ"),
        ],
        caminho_banco=cliente_teste.caminho_db,
    )
    atualizar_status_exigencia(ids[0], "ok", caminho_banco=cliente_teste.caminho_db)

    resposta = cliente_teste.get(f"/processos/{processo_id}/checklist")
    html = resposta.text

    # Só a exigência marcada como "ok" ganha a classe — a pendente não.
    assert html.count("card-exigencia--conferida") == 1
    inicio_conferida = html.index(f'data-exigencia-ids="{ids[0]}"')
    assert "card-exigencia--conferida" in html[max(0, inicio_conferida - 200) : inicio_conferida]
    inicio_pendente = html.index(f'data-exigencia-ids="{ids[1]}"')
    assert "card-exigencia--conferida" not in html[max(0, inicio_pendente - 200) : inicio_pendente]


# ---------- Seção de inconsistências edital-vs-TR (Fase 2, Camada 2) ----------


def test_checklist_inconsistencias_nunca_verificado(cliente_teste):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=cliente_teste.caminho_db)

    html = cliente_teste.get(f"/processos/{processo_id}/checklist").text

    assert "Ainda não verificado." in html
    assert "Verificar inconsistências" in html
    assert 'id="secao-inconsistencias"' in html


def test_checklist_inconsistencias_nao_possivel_mostra_motivo(cliente_teste):
    from app.db.repositorio import atualizar_status_deteccao_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=cliente_teste.caminho_db)
    atualizar_status_deteccao_inconsistencias(
        processo_id,
        comparacao_possivel=False,
        motivo_impossibilidade="não achei o marcador de TR no texto",
        caminho_banco=cliente_teste.caminho_db,
    )

    html = cliente_teste.get(f"/processos/{processo_id}/checklist").text

    assert "Comparação não possível" in html
    assert "não achei o marcador de TR no texto" in html
    assert "Verificar de novo" in html
    # Não é tratado como "sem achados" nem lista achados nenhum.
    assert "Nenhuma inconsistência encontrada" not in html


def test_checklist_inconsistencias_sem_achados_mostra_estado_positivo(cliente_teste):
    from app.db.repositorio import atualizar_status_deteccao_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=cliente_teste.caminho_db)
    atualizar_status_deteccao_inconsistencias(
        processo_id, comparacao_possivel=True, motivo_impossibilidade=None,
        caminho_banco=cliente_teste.caminho_db,
    )

    html = cliente_teste.get(f"/processos/{processo_id}/checklist").text

    assert "Nenhuma inconsistência encontrada" in html
    assert "Comparação não possível" not in html


def test_checklist_inconsistencias_com_achados_agrupa_por_tipo(cliente_teste):
    from app.db.repositorio import atualizar_status_deteccao_inconsistencias, salvar_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=cliente_teste.caminho_db)
    atualizar_status_deteccao_inconsistencias(
        processo_id, comparacao_possivel=True, motivo_impossibilidade=None,
        caminho_banco=cliente_teste.caminho_db,
    )
    salvar_inconsistencias(
        processo_id,
        [
            {
                "tipo": "prazo",
                "descricao": "prazo de entrega diverge",
                "trecho_edital": "O prazo de entrega será de 30 dias.",
                "pagina_edital": 5,
                "trecho_tr": "O prazo de entrega será de 45 dias.",
                "pagina_tr": 20,
            },
            {
                "tipo": "administrativo",
                "descricao": "servidor responsável diverge",
                "trecho_edital": "Fiscal: João da Silva.",
                "pagina_edital": 2,
                "trecho_tr": "Fiscal: Maria Souza.",
                "pagina_tr": 40,
            },
        ],
        caminho_banco=cliente_teste.caminho_db,
    )

    html = cliente_teste.get(f"/processos/{processo_id}/checklist").text

    assert "Prazo" in html
    assert "Administrativo" in html
    assert "O prazo de entrega será de 30 dias." in html
    assert "O prazo de entrega será de 45 dias." in html
    assert "página 5" in html
    assert "página 20" in html
    assert "Fiscal: João da Silva." in html
    assert "Fiscal: Maria Souza." in html
