# Teste de regressão do bug crítico de 15/08/2026: marcar uma exigência
# como conferida não salvava nada -- checklist.js lia
# "checkbox.dataset.exigenciaIds" (undefined, o atributo só existe no
# card pai) em vez de "checkbox.closest('.card-exigencia')
# .dataset.exigenciaIds". Nenhum teste existente pegava isso porque os
# testes de PATCH chamam a rota direto, contornando o JS -- por isso este
# aqui usa Playwright contra um servidor uvicorn REAL e CLICA no checkbox
# de verdade, depois recarrega a página pra confirmar que persistiu (não
# só a marcação visual otimista). Mesma infraestrutura de
# tests/test_perguntas_ui.py e tests/test_lista_processos_ui.py.
#
# Cobre os dois caminhos de código que existem pra isso: exigência avulsa
# (card com 1 checkbox = 1 linha no banco) e grupo de hipóteses (Mudança
# 3 -- 1 checkbox = várias linhas), já que foi essa segunda funcionalidade
# que introduziu o atributo "data-exigencia-ids" no card em vez do input.

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.db.repositorio import criar_arquivo, criar_processo, obter_processo, salvar_exigencias


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    caminho_db = str(tmp_path / "teste.db")
    monkeypatch.setattr("app.db.conexao.DATABASE_PATH", caminho_db)

    from app.main import app

    porta = _porta_livre()
    config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(200):
        try:
            httpx.get(f"http://127.0.0.1:{porta}/", timeout=0.5)
            break
        except httpx.TransportError:
            time.sleep(0.05)
    else:
        raise RuntimeError("servidor de teste não subiu a tempo")

    yield f"http://127.0.0.1:{porta}", caminho_db

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as playwright:
        instancia = playwright.chromium.launch(channel="msedge", headless=True)
        yield instancia
        instancia.close()


def _exigencia_avulsa(caminho_db) -> tuple[int, int]:
    processo_id = criar_processo({"nome": "Processo avulso"}, caminho_banco=caminho_db)
    criar_arquivo(processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db)
    ids = salvar_exigencias(
        processo_id,
        [
            {
                "categoria": "habilitacao_juridica", "descricao": "Exigência avulsa", "trecho": "trecho",
                "arquivo_origem": "edital.pdf", "obrigatorio_para": "todos", "confianca": "localizado",
            }
        ],
        caminho_banco=caminho_db,
    )
    return processo_id, ids[0]


def _exigencias_em_grupo(caminho_db) -> tuple[int, list[int]]:
    processo_id = criar_processo({"nome": "Processo com grupo"}, caminho_banco=caminho_db)
    criar_arquivo(processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db)
    ids = salvar_exigencias(
        processo_id,
        [
            {
                "categoria": "habilitacao_juridica", "descricao": "Hipótese A", "trecho": "trecho A",
                "arquivo_origem": "edital.pdf", "obrigatorio_para": "todos", "confianca": "localizado",
                "grupo_hipoteses": "Documento constitutivo da empresa",
            },
            {
                "categoria": "habilitacao_juridica", "descricao": "Hipótese B", "trecho": "trecho B",
                "arquivo_origem": "edital.pdf", "obrigatorio_para": "todos", "confianca": "localizado",
                "grupo_hipoteses": "Documento constitutivo da empresa",
            },
        ],
        caminho_banco=caminho_db,
    )
    return processo_id, ids


def test_clicar_checkbox_avulso_persiste_apos_recarregar(servidor, navegador):
    base_url, caminho_db = servidor
    processo_id, exigencia_id = _exigencia_avulsa(caminho_db)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist", wait_until="networkidle")
        checkbox = pagina.query_selector(f"#check-{exigencia_id}")
        assert checkbox is not None
        assert not checkbox.is_checked()

        with pagina.expect_response(lambda r: f"/exigencias/{exigencia_id}" in r.url) as info_resposta:
            checkbox.click()
        assert info_resposta.value.status == 200

        # A prova real não é o estado otimista na tela -- é persistir de
        # verdade. Recarrega a página do zero (nova consulta ao banco).
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist", wait_until="networkidle")
        checkbox_recarregado = pagina.query_selector(f"#check-{exigencia_id}")
        assert checkbox_recarregado.is_checked()

        # O card também precisa ter recebido o destaque visual de
        # "conferida" (Mudança 2) -- não só o checkbox interno.
        card = pagina.query_selector(f'[data-exigencia-ids="{exigencia_id}"]')
        assert "card-exigencia--conferida" in card.get_attribute("class")
    finally:
        pagina.close()

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo["exigencias"][0]["status_check"] == "ok"


def test_clicar_checkbox_de_grupo_persiste_todas_as_hipoteses(servidor, navegador):
    base_url, caminho_db = servidor
    processo_id, ids_grupo = _exigencias_em_grupo(caminho_db)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist", wait_until="networkidle")
        checkbox = pagina.query_selector(".checkbox-exigencia")
        assert checkbox is not None
        assert not checkbox.is_checked()

        with pagina.expect_response(lambda r: "/exigencias/" in r.url):
            checkbox.click()
        pagina.wait_for_timeout(300)  # grupo salva 2 linhas em paralelo (Promise.all)

        pagina.goto(f"{base_url}/processos/{processo_id}/checklist", wait_until="networkidle")
        checkbox_recarregado = pagina.query_selector(".checkbox-exigencia")
        assert checkbox_recarregado.is_checked()
    finally:
        pagina.close()

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    # AS DUAS hipóteses do grupo precisam ter sido salvas, não só uma --
    # é exatamente esse "várias linhas por um clique só" que o bug
    # original quebrava.
    status_por_id = {e["id"]: e["status_check"] for e in processo["exigencias"]}
    assert status_por_id[ids_grupo[0]] == "ok"
    assert status_por_id[ids_grupo[1]] == "ok"
