# Testes do módulo de geração de declarações (Fase 4, Camada 1,
# app/geracao/declaracoes.py). Testes de unidade puros -- dicts de
# processo/empresa montados na mão, no formato que
# app.db.repositorio.obter_processo()/obter_empresa() devolvem, sem
# precisar de banco nenhum aqui (isso é coberto pelo teste de integração
# em tests/test_rotas.py, que exercita o caminho real ponta a ponta).

import io
import zipfile

import pytest

from app.geracao.declaracoes import (
    EmpresaSemRepresentanteError,
    SemDeclaracoesError,
    gerar_declaracoes,
)


def _exigencia(categoria: str, descricao: str, trecho: str | None, confianca: str = "localizado") -> dict:
    return {
        "id": 1,
        "categoria": categoria,
        "descricao": descricao,
        "trecho": trecho,
        "confianca": confianca,
    }


def _processo(exigencias: list[dict]) -> dict:
    return {"id": 1, "nome": "Pregão Eletrônico 01/2026", "exigencias": exigencias}


def _empresa(**sobrescreve) -> dict:
    dados = {
        "id": 1,
        "razao_social": "Exemplo Fornecedora de Materiais Ltda",
        "cnpj": "12.345.678/0001-90",
        "representante_legal_nome": "José da Silva Fictício",
        "representante_legal_cargo": "Sócio-administrador",
        "representante_legal_cpf": "000.000.000-00",
    }
    dados.update(sobrescreve)
    return dados


def _textos(documento) -> list[str]:
    return [p.text for p in documento.paragraphs]


def test_gera_preambulo_com_dados_da_empresa():
    processo = _processo([_exigencia("declaracoes_exigidas", "Declaração de menor", "não emprega menor de 18 anos;")])
    documento = gerar_declaracoes(processo, _empresa())

    textos = _textos(documento)
    assert any("Exemplo Fornecedora de Materiais Ltda" in t and "12.345.678/0001-90" in t for t in textos)
    assert any('Pregão Eletrônico 01/2026' in t for t in textos)


def test_cada_declaracao_vira_item_alfabetico_com_trecho_literal():
    processo = _processo(
        [
            _exigencia("declaracoes_exigidas", "Declaração de menor", "não emprega menor de 18 anos;"),
            _exigencia("declaracoes_exigidas", "Declaração de impedimento", "não se enquadra em impedimento;"),
        ]
    )
    documento = gerar_declaracoes(processo, _empresa())

    textos = _textos(documento)
    assert any(t.startswith("a) não emprega menor de 18 anos;") for t in textos)
    assert any(t.startswith("b) não se enquadra em impedimento;") for t in textos)


def test_ignora_exigencias_de_outras_categorias():
    processo = _processo(
        [
            _exigencia("habilitacao_juridica", "Registro comercial", "prova de registro;"),
            _exigencia("declaracoes_exigidas", "Declaração de menor", "não emprega menor de 18 anos;"),
        ]
    )
    documento = gerar_declaracoes(processo, _empresa())

    textos = " ".join(_textos(documento))
    assert "não emprega menor" in textos
    assert "prova de registro" not in textos


def test_sem_trecho_usa_descricao_como_alternativa():
    processo = _processo([_exigencia("declaracoes_exigidas", "Declaração de LGPD", trecho=None)])
    documento = gerar_declaracoes(processo, _empresa())

    textos = _textos(documento)
    assert any("a) Declaração de LGPD" in t for t in textos)


def test_item_inferido_recebe_marca_visivel_e_comentario():
    processo = _processo(
        [_exigencia("declaracoes_exigidas", "Declaração incerta", "algo interpretado pela IA;", confianca="inferido")]
    )
    documento = gerar_declaracoes(processo, _empresa())

    textos = " ".join(_textos(documento))
    assert "CONFERIR" in textos
    assert "inferido" in textos.lower()

    # Comentário nativo do Word de verdade, não só texto solto -- confere
    # que a parte comments.xml existe no pacote OOXML gerado (mesma
    # checagem do smoke test manual que validou a API antes de usar).
    buffer = io.BytesIO()
    documento.save(buffer)
    with zipfile.ZipFile(buffer) as zf:
        assert "word/comments.xml" in zf.namelist()


def test_item_localizado_nao_recebe_marca_de_conferencia():
    processo = _processo(
        [_exigencia("declaracoes_exigidas", "Declaração certa", "texto citado do edital;", confianca="localizado")]
    )
    documento = gerar_declaracoes(processo, _empresa())

    textos = " ".join(_textos(documento))
    assert "CONFERIR" not in textos


def test_bloco_de_assinatura_tem_nome_cargo_e_cpf():
    processo = _processo([_exigencia("declaracoes_exigidas", "D", "trecho;")])
    documento = gerar_declaracoes(processo, _empresa())

    textos = _textos(documento)
    assert any("José da Silva Fictício, Sócio-administrador" in t for t in textos)
    assert any("CPF: 000.000.000-00" in t for t in textos)


def test_sem_declaracoes_exigidas_levanta_erro_claro():
    processo = _processo([_exigencia("habilitacao_juridica", "Registro comercial", "trecho;")])

    with pytest.raises(SemDeclaracoesError, match="Declarações Exigidas"):
        gerar_declaracoes(processo, _empresa())


def test_empresa_sem_representante_legal_levanta_erro_claro():
    processo = _processo([_exigencia("declaracoes_exigidas", "D", "trecho;")])
    empresa_incompleta = _empresa(representante_legal_nome=None)

    with pytest.raises(EmpresaSemRepresentanteError):
        gerar_declaracoes(processo, empresa_incompleta)


def test_mais_de_26_declaracoes_nao_quebra_a_numeracao():
    # Caso extremo (declarações raramente passam de 10-15) -- confere que
    # a letra continua consistente (aa, ab...) em vez de repetir ou
    # quebrar depois de "z".
    exigencias = [
        _exigencia("declaracoes_exigidas", f"Declaração {i}", f"trecho {i};") for i in range(28)
    ]
    documento = gerar_declaracoes(_processo(exigencias), _empresa())

    textos = _textos(documento)
    assert any(t.startswith("z) trecho 25;") for t in textos)
    assert any(t.startswith("aa) trecho 26;") for t in textos)
    assert any(t.startswith("ab) trecho 27;") for t in textos)
