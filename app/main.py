# Ponto de entrada da aplicação FastAPI.
# Por enquanto só existe uma rota de "health check": ela serve para
# confirmar que o servidor subiu e está respondendo, sem nenhuma
# lógica de negócio ainda. As rotas reais (upload, análise, checklist)
# entram nos próximos passos.

from fastapi import FastAPI

app = FastAPI(title="NexLicit Engine")


@app.get("/health")
def health_check():
    # Retorna um status simples em JSON. É o jeito padrão de verificar
    # se uma API está no ar, sem depender de banco de dados ou de IA.
    return {"status": "ok"}
