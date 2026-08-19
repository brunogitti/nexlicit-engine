# Helpers de nome de arquivo pra download (Content-Disposition) --
# extraído de app/rotas/documentos.py (Fase 4, Camada 1) quando a planilha
# de preço (Camada 1 seguinte) precisou exatamente da mesma lógica: nome
# de processo/empresa é texto livre digitado por gente, sem garantia de
# vir limpo pro sistema de arquivos, e o cabeçalho HTTP de download tem a
# mesma regra pros dois tipos de documento gerado (DOCX, XLSX).

import re
from urllib.parse import quote


def nome_arquivo_seguro(base: str) -> str:
    """Troca por "_" qualquer caractere que o Windows não aceita em nome
    de arquivo (\\ / : * ? " < > |) -- razão social e nome de processo são
    texto livre digitado por gente, sem garantia nenhuma de já vir limpo."""
    limpo = re.sub(r'[\\/:*?"<>|]', "_", base).strip()
    return limpo or "documento"


def cabecalho_content_disposition(nome_arquivo: str) -> str:
    """Nome de arquivo com acento (razão social quase sempre tem) precisa
    dos dois formatos no cabeçalho: "filename" simples só aceita ASCII de
    verdade pela RFC 6266 -- por isso um fallback sem acento pra navegador
    mais antigo, e "filename*" com percent-encoding UTF-8 (o formato que a
    RFC define pra isso) pro navegador moderno mostrar o nome certo."""
    ascii_fallback = nome_arquivo.encode("ascii", errors="replace").decode("ascii")
    codificado = quote(nome_arquivo)
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{codificado}"
