# NexLicit Engine

Analisador de editais de licitação para uso interno da NexLicit.

## O problema

Analisar um edital de licitação (Lei 14.133/2021) significa ler dezenas de
páginas espalhadas entre edital, termo de referência, anexos e
esclarecimentos, caçando exigências de habilitação, declarações obrigatórias
e requisitos da proposta — muitas vezes escondidas em meio ao texto. Isso
consome horas e cria risco real de deixar passar uma exigência que inabilita
o fornecedor.

## O que o NexLicit Engine faz

Recebe o conjunto documental de uma licitação (PDF ou DOCX), lê tudo uma
única vez e entrega:

- Checklist de habilitação (jurídica, fiscal/social/trabalhista,
  econômico-financeira e técnica);
- Declarações exigidas;
- Requisitos da proposta;

Cada item vem com o trecho literal e a página de origem, para conferência
rápida. Fases futuras incluem um assistente de perguntas (RAG) sobre os
documentos e um detector de contradições entre eles.

Escopo travado: somente Lei 14.133/2021, todas as modalidades.

## Stack

- **Backend:** Python + FastAPI
- **Frontend:** HTML + Tailwind via Jinja2
- **Leitura de PDF:** PyMuPDF
- **Leitura de DOCX:** python-docx
- **IA:** camada trocável, provedor padrão Gemini Flash (free tier), com
  Claude como alternativa
- **Banco de dados:** SQLite local

## Status

Em desenvolvimento. Fase atual: esqueleto do projeto.

## Configuração após clonar

Este repositório usa um hook de pré-commit versionado (pasta `.githooks/`)
que roda o [gitleaks](https://github.com/gitleaks/gitleaks) para bloquear
commits que contenham segredos (chaves de API, tokens etc.). A pasta padrão
`.git/hooks/` não vai pro Git, então esse passo precisa ser feito manualmente
uma vez em cada máquina:

```powershell
winget install Gitleaks.Gitleaks
git config core.hooksPath .githooks
```

Sem o `gitleaks` instalado, o hook apenas avisa e deixa o commit passar —
ele não substitui a revisão manual antes de commitar.

## Arquitetura e demonstração

<!-- TODO: preencher com print do dashboard e diagrama de arquitetura -->
