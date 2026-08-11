# Este módulo centraliza a leitura das variáveis de ambiente (arquivo .env).
# A ideia é que o resto do código nunca leia o .env diretamente: ele importa
# essas constantes daqui. Assim, se um nome de variável mudar no futuro,
# só precisamos ajustar em um lugar só.

import os
from dotenv import load_dotenv

# Carrega o conteúdo do arquivo .env para as variáveis de ambiente do
# processo Python. Se o .env não existir, isso não gera erro — as
# variáveis simplesmente ficam vazias (None), o que é útil agora que
# ainda não usamos nenhuma delas de verdade.
load_dotenv()

# Provedor de IA que será usado no Passo 3 (ex.: "gemini" ou "claude").
LLM_PROVIDER = os.getenv("LLM_PROVIDER")

# Credenciais e modelo do Gemini (preenchidos no Passo 3).
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")

# Caminho do arquivo do banco SQLite.
DATABASE_PATH = os.getenv("DATABASE_PATH", "./nexlicit.db")

# Trava estrutural contra o incidente da Camada 2 (Fase 2, motor de
# inconsistências): um script de verificação sem "caminho_banco" explícito
# migrou o schema do banco real por efeito colateral — nenhum dado foi
# perdido, mas foi por sorte, não por proteção nenhuma existir.
#
# Precisa estar como "1" pra app/db/conexao.py aceitar abrir conexão no
# caminho configurado em DATABASE_PATH sem um caminho_banco explícito.
# Ausente (ou qualquer outro valor), qualquer chamada que caia no caminho
# padrão do banco real levanta erro alto e claro, em vez de migrar
# silenciosamente o schema nele. O servidor de verdade (uvicorn app.main:app)
# tem essa variável no .env, então continua funcionando normal — quem
# geralmente cai nessa trava é script avulso de verificação/depuração que
# esqueceu de isolar o banco.
USAR_BANCO_REAL = os.getenv("NEXLICIT_USE_BANCO_REAL") == "1"

# Pasta onde os arquivos enviados (editais, TR, anexos) são salvos.
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
