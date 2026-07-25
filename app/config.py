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

# Pasta onde os arquivos enviados (editais, TR, anexos) são salvos.
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
