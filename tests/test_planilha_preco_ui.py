# Teste de clique/digitação real (Playwright) da planilha de preço (Fase
# 4, Camada 1, decisão B — 16/08/2026). Mesma justificativa do bug crítico
# do checkbox de conferência (15/08/2026, ver tests/test_checklist_ui.py):
# um teste que chama a rota PATCH direto contorna qualquer bug no próprio
# JS (ex.: ler "dataset" do elemento errado) — aqui o JS lê
# "tabela.dataset.processoId" e "linha.dataset.numeroItem", exatamente o
# mesmo tipo de leitura que já quebrou uma vez neste projeto. Preenche os
# campos de verdade na tela e recarrega, em vez de simular.

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.db.repositorio import criar_arquivo, criar_processo, salvar_catalogo_itens


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    caminho_db = str(tmp_path / "teste.db")
    monkeypatch.setattr("app.db.conexao.DATABASE_PATH", caminho_db)
    # Servidor real dispara o lifespan do FastAPI de verdade (a limpeza
    # automática de processos antigos, 16/08/2026) -- sem isolar o log de
    # auditoria também, cada rodada da suíte escreveria no
    # logs/limpeza_automatica.log de verdade.
    monkeypatch.setattr("app.limpeza.CAMINHO_LOG_AUDITORIA", str(tmp_path / "limpeza.log"))

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


def _processo_com_catalogo(caminho_db) -> tuple[int, int]:
    processo_id = criar_processo({"nome": "Processo com tabela"}, caminho_banco=caminho_db)
    criar_arquivo(processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db)
    salvar_catalogo_itens(
        processo_id,
        [{"numero": 1, "texto_bruto": "1 Cadeira de rodas UND 5", "pagina": 1, "localizador": "página 1"}],
        caminho_banco=caminho_db,
    )
    return processo_id, 1


def test_preencher_quantidade_e_preco_persiste_apos_recarregar(servidor, navegador):
    base_url, caminho_db = servidor
    processo_id, numero_item = _processo_com_catalogo(caminho_db)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/planilha-preco", wait_until="networkidle")

        linha = pagina.query_selector(f'tr[data-numero-item="{numero_item}"]')
        assert linha is not None
        campo_quantidade = linha.query_selector(".campo-quantidade")
        campo_preco = linha.query_selector(".campo-preco-unitario")
        assert campo_quantidade is not None and campo_preco is not None

        # fill() + Tab real (sai do campo de verdade, dispara o blur nativo
        # do navegador) -- não fetch() direto, nem dispatch_event sintético
        # -- pra pegar em cheio qualquer bug no JS de leitura do dataset,
        # igual ao que aconteceu com o checkbox do checklist.
        campo_quantidade.fill("10")
        with pagina.expect_response(lambda r: f"/itens/{numero_item}/preco" in r.url) as info_quantidade:
            campo_quantidade.press("Tab")
        assert info_quantidade.value.status == 200

        campo_preco.fill("25.5")
        with pagina.expect_response(lambda r: f"/itens/{numero_item}/preco" in r.url) as info_preco:
            campo_preco.press("Tab")
        assert info_preco.value.status == 200

        # A prova real é persistir de verdade -- recarrega do zero.
        pagina.goto(f"{base_url}/processos/{processo_id}/planilha-preco", wait_until="networkidle")
        linha_recarregada = pagina.query_selector(f'tr[data-numero-item="{numero_item}"]')
        assert linha_recarregada is not None
        assert linha_recarregada.query_selector(".campo-quantidade").input_value() == "10"
        assert linha_recarregada.query_selector(".campo-preco-unitario").input_value() == "25.5"
    finally:
        pagina.close()

    from app.db.repositorio import obter_precos_item
    precos = obter_precos_item(processo_id, caminho_banco=caminho_db)
    assert precos[numero_item]["quantidade"] == 10
    assert precos[numero_item]["preco_unitario"] == 25.5
