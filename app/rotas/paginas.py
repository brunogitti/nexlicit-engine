# Rotas que devolvem HTML (Passo 7). Não duplicam lógica de banco: chamam
# as mesmas funções que app/rotas/processos.py e app/rotas/exigencias.py já
# usam para JSON (listar_processos, obter_processo, e o helper
# criar_processo_e_salvar_arquivos). O que muda aqui é só a apresentação —
# renderiza template em vez de devolver dict.
#
# POST /processos/{id}/analisar (Passo 6) não é chamado por este módulo:
# quem dispara a análise de verdade é o JavaScript da própria tela de
# espera (app/static/js embutido em analisando.html), via fetch() no
# navegador. Isso evita duplicar a lógica de "rodar o pipeline e tratar
# erro" — ela já existe, testada, na rota JSON.

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from app.db.repositorio import listar_processos, obter_inconsistencias, obter_processo
from app.extracao.tabela_itens import normalizar_com_mapa
from app.ia.llm_client import CATEGORIAS_CHECKLIST, TIPOS_INCONSISTENCIA
from app.rotas.processos import criar_processo_e_salvar_arquivos
from app.templates_engine import templates

router = APIRouter()

# Rótulo amigável de cada categoria, na ORDEM FIXA da Lei 14.133/2021 — não
# na ordem que vier do banco. As chaves vêm de CATEGORIAS_CHECKLIST
# (app/ia/llm_client.py) para não duplicar essa lista em dois lugares que
# poderiam desalinhar.
RÓTULOS_CATEGORIA = {
    "habilitacao_juridica": "Habilitação Jurídica",
    "habilitacao_fiscal_social_trabalhista": "Habilitação Fiscal, Social e Trabalhista",
    "qualificacao_economico_financeira": "Qualificação Econômico-Financeira",
    "qualificacao_tecnica": "Qualificação Técnica",
    "declaracoes_exigidas": "Declarações Exigidas",
    "requisitos_proposta": "Requisitos da Proposta",
}


RÓTULOS_TIPO_INCONSISTENCIA = {
    "quantidade": "Quantidade",
    "valor": "Valor",
    "prazo": "Prazo",
    "especificacao_tecnica": "Especificação Técnica",
    "administrativo": "Administrativo",
}


RÓTULOS_CATEGORIA_REQUISITO_ITEM = {
    "amostra": "Amostra",
    "registro_sanitario": "Registro sanitário",
    "metrologia": "Metrologia (INMETRO)",
    "normas_tecnicas": "Normas técnicas (ABNT)",
    "laudo": "Laudo técnico",
    "documentacao_produto": "Documentação do produto",
    "garantia": "Garantia",
    "certificacao": "Certificação",
    "assistencia_tecnica": "Assistência técnica",
}

# Passo 9 (Mudança 5): acima desse total de blocos (somando todas as
# categorias) na seção "Requisitos Específicos por Item", os cards de
# categoria começam FECHADOS por padrão — abrir tudo de cara vira uma
# rolagem enorme sem ajudar em nada. Calibrado com dado real dos processos
# já testados: Paulínia tem 16 blocos (fica aberta), Ouroeste tem 43 e
# Frutal tem 280 (as duas fecham) — a separação é bem clara, não tem caso
# de fronteira, então um número redondo no meio serve bem.
LIMITE_BLOCOS_REQUISITOS_ITEM_ABERTO = 20


# Pontuação "decorativa" que aparece como ruído de OCR/extração em volta de
# um texto por lo resto idêntico ('APRESENTAR AMOSTRA*.' vs 'APRESENTAR
# AMOSTRA'.) — aspas retas e curvas, apóstrofo, acento grave, asterisco.
# NÃO inclui vírgula nem ponto no meio do texto: aqueles podem ser separador
# de milhar/decimal ("2.000") ou pontuação de frase de verdade, então
# continuam intactos — só o ponto final (fim de frase) é removido, à parte,
# depois de tirar o resto.
_PADRAO_PONTUACAO_DECORATIVA = re.compile(r"[\"'’‘“”` *]")
_PADRAO_ESPACOS_MULTIPLOS = re.compile(r"\s+")


def _chave_dedup_trecho(trecho: str) -> str:
    """Chave de comparação pra saber se dois trechos são 'o mesmo texto' pra
    fins de agrupar itens que compartilham a mesma exigência (Mudança 1,
    Passo 9): ignora acento/caixa/espaço (normalizar_com_mapa, o mesmo
    normalizador do fatiamento por item do Passo 8) e um conjunto pequeno de
    pontuação decorativa em volta do texto. Não é o texto mostrado na tela —
    isso continua vindo do "trecho" original, com acento/caixa reais."""
    normalizado = normalizar_com_mapa(trecho)[0]
    sem_decoracao = _PADRAO_PONTUACAO_DECORATIVA.sub("", normalizado)
    colapsado = _PADRAO_ESPACOS_MULTIPLOS.sub(" ", sem_decoracao).strip()
    return colapsado.rstrip(".")


def _agrupar_requisitos_por_categoria(requisitos: list[dict]) -> list[dict]:
    """Agrupa os requisitos técnicos por item (Passo 8) por CATEGORIA
    primeiro, não por item (Passo 9/Mudança 1) — um bloco por categoria
    (Amostra, Registro sanitário...), cada um com os textos distintos
    daquela categoria e a lista de itens que compartilham cada texto.

    Por quê: o layout antigo (um bloco por ITEM, com a categoria dentro)
    repetia o MESMO texto de exigência uma vez por item — num edital com,
    por exemplo, "APRESENTAR AMOSTRA." exigido em 223 itens, isso virava 223
    blocos idênticos, página after página sem nenhuma exigência nova sendo
    mostrada. Agrupando por categoria e por texto do trecho (ver
    _chave_dedup_trecho), a mesma informação cabe numa fração do espaço, sem
    perder nenhum item da lista.

    Dedup: dois requisitos da MESMA categoria com texto igual (ignorando
    acento/caixa/espaço/pontuação decorativa) viram UMA entrada, com a lista
    de itens que a compartilham. Se o texto muda de um item pro outro (ex.:
    registro sanitário com especificação própria embutida em cada item),
    cada texto diferente vira sua própria entrada, com seus próprios itens.
    """
    por_categoria: dict[str, dict[str, dict]] = {}
    ordem_encontrada: list[str] = []

    for requisito in requisitos:
        categoria = requisito["categoria"]
        if categoria not in por_categoria:
            por_categoria[categoria] = {}
            ordem_encontrada.append(categoria)

        grupos_da_categoria = por_categoria[categoria]
        chave_trecho = _chave_dedup_trecho(requisito["trecho"])
        if chave_trecho not in grupos_da_categoria:
            grupos_da_categoria[chave_trecho] = {
                "trecho": requisito["trecho"],
                "localizador": requisito.get("localizador"),
                "itens": set(),
            }
        grupos_da_categoria[chave_trecho]["itens"].add(requisito["numero_item"])

    # Ordem fixa (a mesma do YAML de palavras-chave) primeiro; categoria
    # inesperada que não esteja nela entra no fim, na ordem que apareceu —
    # nunca some da tela, só não tem posição fixa reservada.
    ordem_categorias = [c for c in RÓTULOS_CATEGORIA_REQUISITO_ITEM if c in por_categoria]
    ordem_categorias += [c for c in ordem_encontrada if c not in RÓTULOS_CATEGORIA_REQUISITO_ITEM]

    resultado = []
    for categoria in ordem_categorias:
        # Texto mais compartilhado (mais itens) primeiro — é o resumo mais
        # útil de bater o olho; empate quebra pelo menor número de item.
        grupos = sorted(
            por_categoria[categoria].values(),
            key=lambda g: (-len(g["itens"]), min(g["itens"])),
        )
        # União de todos os itens da categoria (não soma dos grupos — um
        # item pode aparecer em mais de um grupo_trecho da mesma categoria,
        # não pode contar duas vezes no resumo "N itens" do cabeçalho).
        itens_da_categoria: set[int] = set()
        for g in grupos:
            itens_da_categoria.update(g["itens"])
        resultado.append(
            {
                "categoria": categoria,
                "rotulo": RÓTULOS_CATEGORIA_REQUISITO_ITEM.get(categoria, categoria),
                "total_itens": len(itens_da_categoria),
                "grupos_trecho": [
                    {
                        "trecho": g["trecho"],
                        "localizador": g["localizador"],
                        "itens": sorted(g["itens"]),
                    }
                    for g in grupos
                ],
            }
        )
    return resultado


def _agrupar_hipoteses(exigencias: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa as exigências de UMA categoria em duas listas:

    - "avulsas": cada uma vira seu próprio card, com seu próprio checkbox —
      o comportamento de sempre.
    - "grupos": exigências alternativas entre si (mesmo "grupo_hipoteses",
      preenchido pela IA — ver app/ia/llm_client.py) viram UM card, com UM
      checkbox só, listando cada hipótese como referência dentro dele.

    Só vira grupo de verdade com 2 ou mais exigências no mesmo
    grupo_hipoteses — uma exigência sozinha marcada, sem par, não tem
    "alternativa" nenhuma pra agrupar, então cai como avulsa mesmo assim.
    """
    por_grupo: dict[str, list[dict]] = {}
    ordem_grupos: list[str] = []
    avulsas: list[dict] = []

    for exigencia in exigencias:
        titulo_grupo = exigencia.get("grupo_hipoteses")
        if not titulo_grupo:
            avulsas.append(exigencia)
            continue
        if titulo_grupo not in por_grupo:
            por_grupo[titulo_grupo] = []
            ordem_grupos.append(titulo_grupo)
        por_grupo[titulo_grupo].append(exigencia)

    grupos: list[dict] = []
    for titulo_grupo in ordem_grupos:
        hipoteses = por_grupo[titulo_grupo]
        if len(hipoteses) < 2:
            avulsas.extend(hipoteses)
            continue
        grupos.append(
            {
                "titulo": titulo_grupo,
                "exigencia_ids": [h["id"] for h in hipoteses],
                "hipoteses": hipoteses,
                "todas_ok": all(h["status_check"] == "ok" for h in hipoteses),
            }
        )

    return grupos, avulsas


def _agrupar_por_categoria(exigencias: list[dict]) -> list[dict]:
    """Agrupa as exigências por categoria, na ordem fixa da lei. Categorias
    sem nenhuma exigência para este processo são omitidas inteiramente —
    nunca mostra uma seção vazia.

    Dentro de cada categoria, ainda separa em "grupos" (exigências
    alternativas entre si, ver _agrupar_hipoteses) e "exigencias" (avulsas,
    cada uma com seu próprio card — nome mantido igual ao de antes pra não
    mexer no resto do template)."""
    por_categoria: dict[str, list[dict]] = {chave: [] for chave in CATEGORIAS_CHECKLIST}
    for exigencia in exigencias:
        chave = exigencia.get("categoria")
        if chave in por_categoria:
            por_categoria[chave].append(exigencia)

    resultado = []
    for chave in CATEGORIAS_CHECKLIST:
        exigencias_categoria = por_categoria[chave]
        if not exigencias_categoria:
            continue
        grupos, avulsas = _agrupar_hipoteses(exigencias_categoria)

        # Contagem "X de Y conferidas" do cabeçalho recolhível (Mudança 5):
        # mesmo critério da barra de progresso do topo da página — conta
        # exigência de VERDADE (linha do banco), não card. Um grupo de
        # hipóteses vale pelas N exigências que ele representa, não 1.
        total = len(avulsas) + sum(len(g["exigencia_ids"]) for g in grupos)
        feitas = sum(1 for e in avulsas if e["status_check"] == "ok")
        feitas += sum(len(g["exigencia_ids"]) for g in grupos if g["todas_ok"])

        resultado.append(
            {
                "chave": chave,
                "rotulo": RÓTULOS_CATEGORIA[chave],
                "grupos": grupos,
                "exigencias": avulsas,
                "total": total,
                "feitas": feitas,
            }
        )
    return resultado


def _agrupar_inconsistencias_por_tipo(achados: list[dict]) -> list[dict]:
    """Agrupa os achados do motor de inconsistências (Fase 2) por tipo, na
    ordem fixa de TIPOS_INCONSISTENCIA — mesmo padrão de
    _agrupar_por_categoria. Tipo sem nenhum achado pra este processo é
    omitido, nunca mostra grupo vazio."""
    por_tipo: dict[str, list[dict]] = {chave: [] for chave in TIPOS_INCONSISTENCIA}
    for achado in achados:
        chave = achado.get("tipo")
        if chave in por_tipo:
            por_tipo[chave].append(achado)

    resultado = []
    for chave in TIPOS_INCONSISTENCIA:
        achados_tipo = por_tipo[chave]
        if not achados_tipo:
            continue
        resultado.append({"chave": chave, "rotulo": RÓTULOS_TIPO_INCONSISTENCIA[chave], "achados": achados_tipo})
    return resultado


def _estado_inconsistencias(processo: dict, achados: list[dict]) -> dict:
    """Decide qual dos 4 estados da seção de inconsistências (Fase 2,
    Camada 2) a tela deve mostrar, a partir do status persistido em
    "processo" (colunas inconsistencias_*, ver schema.sql) e dos achados já
    salvos.

    As colunas só existem porque a tabela "inconsistencia" sozinha (só
    achados) não basta pra diferenciar "nunca verificado" de "verificado,
    sem achado nenhum" — os dois têm zero linhas em "inconsistencia".
    """
    if processo.get("inconsistencias_verificado_em") is None:
        return {"estado": "nunca_verificado"}

    if not processo.get("inconsistencias_comparacao_possivel"):
        return {
            "estado": "nao_possivel",
            "motivo": processo.get("inconsistencias_motivo_impossibilidade"),
        }

    if not achados:
        return {"estado": "sem_achados"}

    return {"estado": "com_achados", "grupos": _agrupar_inconsistencias_por_tipo(achados)}


def _formatar_data_criacao(criado_em_iso: str) -> str:
    """Converte o "criado_em" gravado (ISO 8601, sempre UTC — ver
    repositorio._agora_iso) pro formato dd/mm/aaaa em fuso local, pro
    card da listagem. Guardar em UTC e formatar em local na exibição é
    o mesmo princípio já usado pro resto do projeto: consistência no
    banco, conversão só na hora de mostrar pra gente."""
    momento = datetime.fromisoformat(criado_em_iso)
    return momento.astimezone().strftime("%d/%m/%Y")


@router.get("/")
def painel_principal(request: Request):
    """Painel principal (histórico de processos) — a tela que você vê ao
    abrir o app. GET /processos continua sendo só o contrato JSON do Passo
    6 (ver app/rotas/processos.py); esta é a rota própria da versão HTML,
    sem negociação de conteúdo nenhuma."""
    processos = listar_processos()

    # Cada card mostra "X de Y exigências conferidas". listar_processos()
    # não traz exigências (é intencionalmente leve, ver repositorio.py),
    # então busca o detalhe de cada processo aqui. Para o volume de
    # processos de uso local esperado, N+1 consultas não é problema; não
    # vale a complexidade de uma query agregada só pra isso.
    processos_com_progresso = []
    for processo in processos:
        detalhe = obter_processo(processo["id"])
        exigencias = detalhe["exigencias"] if detalhe else []
        processos_com_progresso.append(
            {
                **processo,
                "total_exigencias": len(exigencias),
                "exigencias_feitas": sum(1 for e in exigencias if e["status_check"] == "ok"),
                "criado_em_formatado": _formatar_data_criacao(processo["criado_em"]),
                # texto simples pra busca client-side (lista_processos.js)
                # filtrar sem precisar ler vários elementos do DOM — já
                # normalizado (minúsculo) aqui, não no JS.
                "busca_texto": f"{processo['nome']} {processo.get('orgao') or ''}".lower(),
            }
        )

    return templates.TemplateResponse(
        request, "lista_processos.html", {"processos": processos_com_progresso}
    )


@router.get("/processos/novo")
def formulario_novo_processo(request: Request):
    return templates.TemplateResponse(request, "novo_processo.html", {})


@router.post("/processos/novo")
async def criar_processo_via_formulario(
    nome: Annotated[str, Form()],
    orgao: Annotated[str | None, Form()] = None,
    modalidade: Annotated[str | None, Form()] = None,
    objeto: Annotated[str | None, Form()] = None,
    valor_estimado: Annotated[float | None, Form()] = None,
    data_sessao: Annotated[str | None, Form()] = None,
    plataforma: Annotated[str | None, Form()] = None,
    arquivos: Annotated[list[UploadFile], File()] = [],
):
    """Cria o processo e os arquivos (mesma lógica de POST /processos, via
    helper compartilhado) e manda para a tela de espera, que é quem de fato
    dispara a análise (via JavaScript, chamando a rota já existente)."""
    processo_id = criar_processo_e_salvar_arquivos(
        {
            "nome": nome,
            "orgao": orgao,
            "modalidade": modalidade,
            "objeto": objeto,
            "valor_estimado": valor_estimado,
            "data_sessao": data_sessao,
            "plataforma": plataforma,
        },
        arquivos,
    )
    return RedirectResponse(f"/processos/{processo_id}/analisando", status_code=303)


@router.get("/processos/{id}/analisando")
def tela_de_espera(request: Request, id: int):
    return templates.TemplateResponse(request, "analisando.html", {"processo_id": id})


@router.get("/processos/{id}/checklist")
def tela_checklist(request: Request, id: int):
    processo = obter_processo(id)
    if processo is None:
        return templates.TemplateResponse(
            request,
            "erro_pagina.html",
            {"mensagem": f"Processo {id} não encontrado"},
            status_code=404,
        )

    # O trecho de cada exigência aponta pro arquivo de origem por id
    # (arquivo_origem_id, a FK) — resolve pro nome do arquivo aqui, uma vez,
    # em vez de fazer isso dentro do template.
    nome_por_arquivo_id = {arquivo["id"]: arquivo["nome_arquivo"] for arquivo in processo["arquivos"]}
    for exigencia in processo["exigencias"]:
        exigencia["nome_arquivo_origem"] = nome_por_arquivo_id.get(exigencia.get("arquivo_origem_id"))

    total = len(processo["exigencias"])
    feitas = sum(1 for e in processo["exigencias"] if e["status_check"] == "ok")
    percentual = round(feitas / total * 100) if total else 0

    requisitos_por_categoria = _agrupar_requisitos_por_categoria(processo["requisitos_item"])

    # Mudança 5: decisão de abrir/fechar por padrão é UMA SÓ, pra seção
    # inteira — olha o total de blocos somando todas as categorias, não
    # categoria por categoria (uma categoria com 3 blocos não teria motivo
    # pra começar fechada sozinha se as outras 7 têm 30 cada).
    total_blocos_requisitos_item = sum(len(b["grupos_trecho"]) for b in requisitos_por_categoria)
    requisitos_item_aberto_por_padrao = total_blocos_requisitos_item <= LIMITE_BLOCOS_REQUISITOS_ITEM_ABERTO

    # Fase 2 (motor de inconsistências), Camada 2.
    achados_inconsistencia = obter_inconsistencias(id)
    estado_inconsistencias = _estado_inconsistencias(processo, achados_inconsistencia)

    return templates.TemplateResponse(
        request,
        "checklist.html",
        {
            "processo": processo,
            "grupos": _agrupar_por_categoria(processo["exigencias"]),
            "requisitos_por_categoria": requisitos_por_categoria,
            "requisitos_item_aberto_por_padrao": requisitos_item_aberto_por_padrao,
            "total": total,
            "feitas": feitas,
            "percentual": percentual,
            "inconsistencias": estado_inconsistencias,
        },
    )
