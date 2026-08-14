# Teste de ponta a ponta da busca client-side na listagem de processos
# (Passo 7, polimento de necessidade real) via Playwright contra um
# servidor uvicorn REAL — TestClient não executa JavaScript, e é o JS de
# app/static/js/lista_processos.js que filtra os cards na tela. Mesmo
# padrão de infraestrutura de tests/test_perguntas_ui.py (servidor numa
# thread, banco de teste isolado, canal "msedge" já instalado).

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.db.repositorio import criar_processo


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


def test_busca_filtra_por_nome_e_orgao_e_mostra_aviso_sem_resultado(servidor, navegador):
    base_url, caminho_db = servidor
    criar_processo({"nome": "Pregão 82/2026", "orgao": "Prefeitura de Paulínia"}, caminho_banco=caminho_db)
    criar_processo({"nome": "Pregão 60/2026", "orgao": "Prefeitura de Frutal"}, caminho_banco=caminho_db)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/", wait_until="networkidle")

        cards = pagina.query_selector_all(".card-processo")
        assert len(cards) == 2

        # Busca por parte do nome de só um processo.
        pagina.fill("#busca-processos-input", "82/2026")
        visiveis = [c for c in pagina.query_selector_all(".card-processo") if c.is_visible()]
        assert len(visiveis) == 1
        assert "Pregão 82/2026" in visiveis[0].inner_text()
        assert pagina.is_hidden("#busca-sem-resultado")

        # Busca por órgão (não pelo nome) — confirma que filtra pelos dois campos.
        pagina.fill("#busca-processos-input", "Frutal")
        visiveis = [c for c in pagina.query_selector_all(".card-processo") if c.is_visible()]
        assert len(visiveis) == 1
        assert "Pregão 60/2026" in visiveis[0].inner_text()

        # Busca sem nenhuma correspondência.
        pagina.fill("#busca-processos-input", "não existe nenhum processo assim")
        visiveis = [c for c in pagina.query_selector_all(".card-processo") if c.is_visible()]
        assert len(visiveis) == 0
        assert pagina.is_visible("#busca-sem-resultado")

        # Limpar a busca mostra todos de novo.
        pagina.fill("#busca-processos-input", "")
        visiveis = [c for c in pagina.query_selector_all(".card-processo") if c.is_visible()]
        assert len(visiveis) == 2
        assert pagina.is_hidden("#busca-sem-resultado")
    finally:
        pagina.close()
