# Bartho — Assistente administrativo com IA para clínicas

O Bartho é um projeto de Produto que investiga como um assistente conversacional com Inteligência Artificial pode resolver jornadas administrativas de uma clínica de ponta a ponta, reduzindo a necessidade de intervenção humana.

O protótipo combina interpretação de linguagem natural, regras de negócio e execução de ações por meio de ferramentas para apoiar tarefas como consulta de disponibilidade, agendamento, remarcação, cancelamento de consultas e esclarecimento de dúvidas administrativas.

## 🎯 Sobre o projeto

O Bartho foi desenvolvido como um case de Produto, contemplando etapas do processo de construção de uma solução digital:

- **Definição do problema:** identificação da oportunidade de automatizar jornadas administrativas de uma clínica.
- **Hipótese de produto:** investigação do potencial de um assistente conversacional para resolver solicitações sem intervenção humana.
- **Definição e priorização do MVP:** seleção das funcionalidades prioritárias para validar a proposta.
- **Prototipação:** construção de uma solução funcional para experimentar as jornadas.
- **Métricas:** definição de indicadores para avaliar a capacidade de resolução do assistente.
- **Testes:** avaliação do comportamento do protótipo em diferentes cenários.
- **Análise dos resultados:** identificação de limitações, aprendizados e oportunidades de evolução.

## 📊 Resultados do MVP

Em uma rodada exploratória com 20 participantes, foram observados os seguintes resultados:

- **17 de 20 participantes** concluíram a jornada sem intervenção humana.
- **85% de resolução autônoma observada** nessa rodada de testes.

Os testes também evidenciaram limitações relacionadas principalmente à latência e à manutenção de contexto em interações mais complexas.

Os resultados representam uma validação exploratória do protótipo e não devem ser interpretados como indicadores de desempenho em produção.

## 🛠️ Tecnologias e conceitos

| Tecnologia ou conceito | Aplicação no projeto |
|---|---|
| Python | Desenvolvimento do backend e da lógica do assistente. |
| Qwen | Modelo de linguagem utilizado no protótipo. |
| Ollama | Serviço utilizado para disponibilizar o modelo de linguagem, conforme a configuração do ambiente. |
| Flask | Construção da API do backend. |
| SQLite | Armazenamento de dados estruturados. |
| RAG | Recuperação de informações de uma base de conhecimento. |
| Tools | Execução de consultas e ações por meio de ferramentas. |
| Regras de negócio | Validação das condições necessárias para executar as jornadas. |
| HTML, CSS e JavaScript | Construção da interface web. |
| GitHub Pages | Hospedagem da interface e da apresentação. |
| Render | Hospedagem do backend. |

## 🧠 Perspectiva de Produto

Além da implementação técnica, o projeto explora decisões relacionadas à experiência do usuário, ao escopo do MVP e à mensuração de valor.

A principal métrica é a **taxa de resolução autônoma**, que permite avaliar quantas jornadas o assistente consegue concluir corretamente sem intervenção humana.

O projeto também considera indicadores complementares:

- Conclusão correta por jornada.
- Necessidade de intervenção humana.
- Taxa de abandono.
- Falhas na execução de ferramentas.
- Tempo de resposta.
- Motivos de não resolução.

Uma mensagem respondida não representa necessariamente uma solicitação resolvida. A avaliação deve considerar se a necessidade do usuário foi atendida corretamente.

## 🏗️ Arquitetura da solução

O projeto utiliza componentes com responsabilidades distintas:

1. **Interface web:** recebe as mensagens do usuário e apresenta as respostas do assistente.
2. **API Flask:** recebe as solicitações e coordena o processamento no backend.
3. **Modelo de linguagem:** auxilia na interpretação das mensagens em linguagem natural.
4. **Lógica de negócio e ferramentas:** organizam as jornadas, validam informações e executam ações.
5. **Banco de dados:** permite armazenar dados estruturados utilizados pela aplicação.

O funcionamento efetivo depende da integração entre esses componentes e deve ser validado por testes de ponta a ponta.

## 🚀 Case completo

O case apresenta o processo de Produto por trás do Bartho, incluindo:

- Problema e hipótese.
- Escopo e priorização do MVP.
- Funcionamento e arquitetura da solução.
- Decisões de Produto.
- Métricas e resultados.
- Testes, limitações e aprendizados.
- Oportunidades de evolução.

O objetivo é demonstrar como conceitos de Produto podem ser aplicados à construção e à validação de uma solução baseada em Inteligência Artificial.

## 🌐 Publicação

O projeto utiliza dois serviços de hospedagem.

| Componente | Serviço |
|---|---|
| Interface web e apresentação | GitHub Pages |
| API e backend | Render |

**Apresentação e interface:**

https://anavieira00-product.github.io/Bartho-assistente-virtual/

**Backend:**

https://bartho-assistente-virtual.onrender.com

O backend disponibiliza os endpoints `/health` e `/mensagem`. O endpoint `/health` permite verificar a disponibilidade básica do serviço, enquanto `/mensagem` recebe as solicitações enviadas pela interface.

A disponibilidade da API não garante, por si só, que todas as funcionalidades estejam funcionando corretamente.

## ⚙️ Configuração do backend

### Arquivos principais

- `api.py`: API Flask com os endpoints `/health` e `/mensagem`.
- `bartho.py`: lógica do assistente, conforme a implementação.
- `agente.py`: componentes relacionados à lógica do agente.
- `rag.py`: componentes relacionados à recuperação de informações.
- `tools.py`: ferramentas utilizadas pelo assistente.
- `requirements.txt`: dependências Python.
- `render.yaml`: configuração inicial do serviço Render.

A estrutura real pode conter outros módulos necessários ao funcionamento da aplicação.

### Configuração do Render

**Build Command:**

```bash
pip install -r requirements.txt
```

**Start Command:**

```bash
gunicorn api:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

### Variáveis de ambiente

As seguintes variáveis podem ser necessárias no painel de configuração do Render:

| Variável | Finalidade |
|---|---|
| `BARTHO_MODO_WEB` | Configuração do modo de execução web. |
| `BARTHO_ALLOWED_ORIGIN` | Definição da origem autorizada para acessar a API. |
| `BARTHO_SECRET_KEY` | Chave secreta da aplicação. |
| `OLLAMA_BASE_URL` | Endereço-base do serviço de modelo. |
| `OLLAMA_MODEL` | Nome exato do modelo disponível no serviço. |
| `OLLAMA_API_KEY` | Credencial do provedor, quando exigida. |
| `OLLAMA_TIMEOUT_SECONDS` | Tempo limite das chamadas, conforme a implementação. |

Os valores devem corresponder à configuração real do backend e do provedor utilizado.

**Segurança:** não publique chaves de API, tokens ou outros dados sensíveis no repositório. Configure as credenciais como variáveis secretas no painel do serviço de hospedagem.

### Cuidados com a hospedagem

- O uso de um modelo fora do computador local exige um serviço acessível pela internet e compatível com a configuração do backend.
- O nome do modelo configurado precisa corresponder a um modelo efetivamente disponível no provedor.
- O armazenamento do plano gratuito do Render pode ser efêmero. Dados gravados em SQLite podem ser perdidos quando a instância reiniciar ou for substituída.
- Utilize dados fictícios nos testes até garantir condições adequadas de persistência e proteção dos dados.
- A configuração inicial de hospedagem pode exigir ajustes após os primeiros testes.
- Antes de considerar o assistente funcional publicamente, valide a interpretação das mensagens, a execução das ferramentas e a conclusão das jornadas.

## 🔭 Próximos passos

A evolução do Bartho pode ser organizada em quatro frentes:

**1. Confiabilidade**

Melhorar a interpretação das solicitações, a preservação do contexto e a validação das respostas.

**2. Execução das jornadas**

Garantir que as ferramentas sejam acionadas corretamente e que as operações respeitem as regras de negócio.

**3. Observabilidade**

Acompanhar falhas, tempo de resposta, erros de ferramentas e motivos de não resolução.

**4. Avaliação de Produto**

Executar cenários padronizados, medir a conclusão correta das jornadas e priorizar melhorias de acordo com o impacto na experiência do usuário.

---

*Projeto desenvolvido como protótipo e estudo de Product Management aplicado à Inteligência Artificial Generativa.*
