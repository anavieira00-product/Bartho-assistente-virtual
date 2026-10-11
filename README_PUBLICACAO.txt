BARTHO — BACKEND PREPARADO PARA HOSPEDAGEM

IMPORTANTE
- Este pacote não inclui bartho.db nem arquivos de métricas, para evitar publicar dados antigos ou dados potencialmente pessoais. O código cria uma base nova de demonstração na primeira inicialização.
- O código mantém o uso local do Ollama como padrão. Para executar a IA fora do seu computador, configure um serviço de modelo acessível pela internet e confirme que o modelo escolhido está disponível nele.
- A configuração inicial usa https://ollama.com como endpoint de exemplo e qwen3:1.7b como nome de modelo, mas a disponibilidade desse modelo na conta/provedor NÃO está garantida. Se o provedor não oferecer esse modelo, será necessário ajustar OLLAMA_MODEL para um modelo disponível e compatível.
- Nunca coloque chaves de API neste repositório. Configure OLLAMA_API_KEY e BARTHO_SECRET_KEY como variáveis secretas no painel do Render.
- O plano gratuito do Render usa armazenamento efêmero. Os agendamentos gravados em SQLite podem ser perdidos quando a instância reiniciar. Use somente dados fictícios de demonstração até configurar armazenamento persistente adequado.

ARQUIVOS
- api.py: API Flask com /health e /mensagem
- bartho.py, agente.py, rag.py, tools.py e demais módulos: lógica do assistente
- requirements.txt: dependências Python
- render.yaml: configuração inicial de serviço Render

COMANDOS RENDER
Build Command: pip install -r requirements.txt
Start Command: gunicorn api:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120

VARIÁVEIS PARA CONFIGURAR NO RENDER
BARTHO_MODO_WEB = 1
BARTHO_ALLOWED_ORIGIN = https://anavieira00-product.github.io
BARTHO_SECRET_KEY = gerar uma chave aleatória longa e privada
OLLAMA_BASE_URL = endereço-base do provedor de modelo
OLLAMA_MODEL = nome exato do modelo disponível no provedor
OLLAMA_API_KEY = chave secreta do provedor, quando exigida
OLLAMA_TIMEOUT_SECONDS = 90

OBSERVAÇÃO TÉCNICA
O backend pode precisar de ajustes adicionais depois do primeiro teste de hospedagem. O modelo e as chamadas de IA precisam ser testados antes de considerar o Bartho funcional publicamente.
