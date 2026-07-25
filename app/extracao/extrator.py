# Módulo de extração de texto de PDF e DOCX.
# Não depende de FastAPI, banco ou IA: recebe um caminho de arquivo e devolve
# o texto extraído junto com a localização de origem (página ou parágrafo).

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, cast

import fitz  # PyMuPDF
from docx import Document


# Tamanho mínimo (em caracteres, já sem espaços nas bordas) para considerar que
# uma página de PDF tem texto de verdade. Abaixo disso, marcamos como possível
# página escaneada (sem OCR: só um alerta, conferido por humano).
LIMITE_TEXTO_PAGINA_VAZIA = 10


@dataclass
class Bloco:
    """Um trecho de texto localizado dentro do documento."""

    # Número físico da página no PDF (1, 2, 3...). None para DOCX, porque
    # DOCX não guarda número de página no arquivo (isso é calculado só na
    # hora de exibir/imprimir o documento no Word).
    pagina: Optional[int]

    # Descrição da localização, para um humano conferir contra o arquivo
    # original: "página N" (PDF) ou "parágrafo N" (DOCX).
    localizador: str

    texto: str

    # PONTO DE ATENÇÃO PARA O FUTURO: quando o validador for comparar um
    # trecho literal vindo da IA contra este `texto`, a comparação vai
    # precisar de espaços normalizados (múltiplos espaços/quebras de linha
    # viram um só, por exemplo). Essa normalização NÃO está implementada
    # aqui de propósito — entra como uma etapa separada mais pra frente,
    # provavelmente aplicada nos dois lados (texto do documento e trecho da
    # IA) antes de comparar.


@dataclass
class Alerta:
    """Um aviso sobre algo que merece conferência humana."""

    pagina: Optional[int]
    mensagem: str


@dataclass
class DocumentoExtraido:
    """Resultado da extração de um arquivo inteiro."""

    nome_arquivo: str
    tipo: Literal["pdf", "docx"]
    num_paginas: Optional[int]  # só para PDF; None para DOCX
    blocos: list[Bloco]
    alertas: list[Alerta]


def extrair_texto(caminho: str) -> DocumentoExtraido:
    """Detecta o tipo do arquivo pela extensão e extrai o texto."""

    caminho_arquivo = Path(caminho)
    extensao = caminho_arquivo.suffix.lower()

    if extensao == ".pdf":
        return _extrair_pdf(caminho_arquivo)
    elif extensao == ".docx":
        return _extrair_docx(caminho_arquivo)
    else:
        raise ValueError(f"formato não suportado: {extensao}")


def _extrair_pdf(caminho_arquivo: Path) -> DocumentoExtraido:
    blocos: list[Bloco] = []
    alertas: list[Alerta] = []

    documento = fitz.open(caminho_arquivo)
    try:
        for indice in range(documento.page_count):
            pagina = documento[indice]
            # get_text() sem argumento usa o padrão "text", que sempre devolve
            # uma string. O cast é só para o verificador de tipos: a própria
            # biblioteca não anota isso, então ele enxerga um tipo mais amplo
            # (string, lista ou dicionário, dependendo da opção usada).
            texto = cast(str, pagina.get_text())

            # Número físico da página: índice do PyMuPDF começa em 0, mas a
            # referência de conferência humana é 1, 2, 3...
            numero_pagina = indice + 1

            blocos.append(
                Bloco(
                    pagina=numero_pagina,
                    localizador=f"página {numero_pagina}",
                    texto=texto,
                )
            )

            if len(texto.strip()) < LIMITE_TEXTO_PAGINA_VAZIA:
                alertas.append(
                    Alerta(
                        pagina=numero_pagina,
                        mensagem=(
                            f"possível página escaneada (página {numero_pagina} "
                            "veio sem texto extraível ou quase vazia)"
                        ),
                    )
                )

        num_paginas = documento.page_count
    finally:
        documento.close()

    return DocumentoExtraido(
        nome_arquivo=caminho_arquivo.name,
        tipo="pdf",
        num_paginas=num_paginas,
        blocos=blocos,
        alertas=alertas,
    )


def _extrair_docx(caminho_arquivo: Path) -> DocumentoExtraido:
    blocos: list[Bloco] = []

    # python-docx espera uma string (ou um arquivo binário aberto), não um Path.
    documento = Document(str(caminho_arquivo))

    for indice, paragrafo in enumerate(documento.paragraphs):
        numero_paragrafo = indice + 1
        blocos.append(
            Bloco(
                # DOCX não tem página confiável (ver comentário na classe
                # Bloco): usamos o índice do parágrafo como localizador.
                pagina=None,
                localizador=f"parágrafo {numero_paragrafo}",
                texto=paragrafo.text,
            )
        )

    return DocumentoExtraido(
        nome_arquivo=caminho_arquivo.name,
        tipo="docx",
        num_paginas=None,
        blocos=blocos,
        alertas=[],
    )
