import json
import os
import re
import urllib.request


# ==================================================
# CONFIGURAÇÃO
# ==================================================

# Local development keeps working as before. In hosted deployments, configure:
# OLLAMA_BASE_URL (for example https://ollama.com), OLLAMA_API_KEY and OLLAMA_MODEL.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_URL = os.environ.get("OLLAMA_URL", OLLAMA_BASE_URL + "/api/generate")
MODELO = os.environ.get("OLLAMA_MODEL", "qwen3:1.7b")




def _interpretacao_basica(mensagem):
    """
    Plano de contingência para mensagens simples quando o modelo
    retornar uma resposta vazia ou fora do formato JSON esperado.
    Não consulta disponibilidade nem executa agendamentos.
    """
    texto = (mensagem or "").strip().lower()
    texto_sem_acentos = (
        texto.replace("á", "a").replace("à", "a").replace("ã", "a")
        .replace("â", "a").replace("é", "e").replace("ê", "e")
        .replace("í", "i").replace("ó", "o").replace("ô", "o")
        .replace("õ", "o").replace("ú", "u").replace("ç", "c")
    )

    saudacao = bool(re.search(
        r"\b(oi|ola|bom dia|boa tarde|boa noite|e ai)\b",
        texto_sem_acentos
    ))

    intencao = "nao_informado"
    if any(x in texto_sem_acentos for x in ("cancelar", "cancela", "desmarcar", "desmarca")):
        intencao = "cancelar"
    elif any(x in texto_sem_acentos for x in ("remarcar", "reagendar", "mudar minha consulta", "trocar meu horario")):
        intencao = "remarcar"
    elif any(x in texto_sem_acentos for x in ("agendar", "marcar consulta", "quero marcar", "quero uma consulta", "marque uma consulta")):
        intencao = "agendar"
    elif any(x in texto_sem_acentos for x in ("quais especialidades", "especialidades a clinica", "especialidades voces", "que especialidades")):
        intencao = "listar_especialidades"
    elif any(x in texto_sem_acentos for x in ("tem horario", "disponibilidade", "horarios disponiveis", "tem vaga")):
        intencao = "consultar"

    especialidades = []
    if any(x in texto_sem_acentos for x in ("cardiologia", "cardiologista", "medico do coracao", "medico de coracao")):
        especialidades.append("cardiologia")
    if "dermatologia" in texto_sem_acentos or "dermatologista" in texto_sem_acentos:
        especialidades.append("dermatologia")
    if any(x in texto_sem_acentos for x in ("clinica geral", "clinico geral")):
        especialidades.append("clinica_geral")

    if intencao == "nao_informado" and especialidades:
        if any(x in texto_sem_acentos for x in ("tem ", "vocês têm", "voces tem", "quem atende", "qual medico")):
            intencao = "consultar_medico"

    data = "nao_informado"
    for palavra in ("amanha", "hoje", "segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"):
        if re.search(r"\b" + palavra + r"\b", texto_sem_acentos):
            data = "terça" if palavra == "terca" else palavra
            break

    horario = "nao_informado"
    hora_match = re.search(r"\b(?:as\s*)?(\d{1,2})(?:h|:\s*(\d{2}))\b", texto_sem_acentos)
    if hora_match:
        hora = int(hora_match.group(1))
        minuto = int(hora_match.group(2) or 0)
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            horario = f"{hora:02d}:{minuto:02d}"
    elif "manha" in texto_sem_acentos:
        horario = "manha"
    elif "tarde" in texto_sem_acentos or "depois do almoco" in texto_sem_acentos:
        horario = "tarde"

    consultas = [
        {"especialidade": esp, "data": data, "horario": horario}
        for esp in especialidades
    ]

    return {
        "saudacao": saudacao,
        "intencao": intencao,
        "especialidade": especialidades[0] if especialidades else "nao_informado",
        "especialidades": especialidades,
        "consultas": consultas,
        "data": data,
        "horario": horario,
    }


# ==================================================
# INTERPRETAR MENSAGEM
# ==================================================

def interpretar_mensagem(mensagem):

    prompt = f"""
Você é o interpretador de linguagem natural do Bartho,
um assistente virtual de uma clínica médica.

Sua tarefa é APENAS interpretar a mensagem do usuário.

Você NÃO deve:
- responder ao usuário;
- inventar disponibilidade;
- inventar médicos;
- inventar datas;
- executar agendamentos.

Você deve somente extrair informações da mensagem
e retornar os dados no formato solicitado.


==================================================
1. SAUDAÇÃO
==================================================

O campo "saudacao" deve ser:

true = existe uma saudação na mensagem
false = não existe saudação

Exemplos de saudação:

"Oi"
"Olá"
"Bom dia"
"Boa tarde"
"Boa noite"
"Oi, tudo bem?"

IMPORTANTE:

A saudação NÃO substitui a intenção principal.

Exemplo:

"Oi, quero marcar cardiologista."

Resultado:

saudacao = true
intencao = agendar


==================================================
2. INTENÇÕES
==================================================

Escolha UMA intenção principal:

listar_especialidades
consultar_medico
consultar
agendar
remarcar
cancelar
nao_informado


--------------------------------------------------
listar_especialidades
--------------------------------------------------

Use quando a pessoa quiser saber quais
especialidades a clínica oferece.

Exemplos:

"Quais especialidades vocês têm?"

"Quais especialidades a clínica oferece?"

"Que tipos de consulta vocês têm?"


--------------------------------------------------
consultar_medico
--------------------------------------------------

Use quando a pessoa perguntar se existe determinada
especialidade ou qual médico atende determinada
especialidade.

Exemplos:

"Vocês têm cardiologista?"

"Tem dermatologista?"

"Quem atende dermatologia?"

"Qual médico atende cardiologia?"

"Que dia o cardiologista atende?"


--------------------------------------------------
consultar
--------------------------------------------------

Use quando a pessoa quiser consultar disponibilidade,
datas ou horários.

Exemplos:

"Tem cardiologista amanhã?"

"Tem horário para dermatologista terça?"

"Tem algum horário depois do almoço?"

"Quais horários estão disponíveis?"

"Tem dermatologista terça às 10?"


--------------------------------------------------
agendar
--------------------------------------------------

Use quando a pessoa disser que deseja marcar
uma ou mais consultas.

Exemplos:

"Quero marcar cardiologista."

"Quero agendar dermatologia."

"Marca uma consulta para mim."

"Quero marcar dermatologia e clínica geral."

"Quero cardiologia, dermatologia e clínica geral."


--------------------------------------------------
remarcar
--------------------------------------------------

Use quando a pessoa quiser alterar uma consulta
que já existe.

Exemplos:

"Quero remarcar minha consulta."

"Quero mudar minha consulta."

"Pode trocar meu horário?"

"Quero alterar a data da consulta."


--------------------------------------------------
cancelar
--------------------------------------------------

Use quando a pessoa quiser cancelar uma consulta.

Exemplos:

"Quero cancelar minha consulta."

"Desmarca minha consulta."

"Não vou conseguir ir, quero cancelar."


--------------------------------------------------
nao_informado
--------------------------------------------------

Use quando não for possível identificar com segurança
nenhuma das intenções anteriores.

Também use quando houver somente uma saudação.

Exemplo:

"Oi"

saudacao = true
intencao = nao_informado


==================================================
3. ESPECIALIDADES
==================================================

A clínica trabalha com estas especialidades:

cardiologia
dermatologia
clinica_geral


Existem DOIS campos:

"especialidade"
"especialidades"


--------------------------------------------------
CAMPO especialidades
--------------------------------------------------

O campo "especialidades" deve ser SEMPRE uma lista.

Ele deve conter TODAS as especialidades explicitamente
mencionadas pelo usuário.

Exemplo:

"Quero dermatologia."

especialidades = ["dermatologia"]


Exemplo:

"Quero dermatologia e clínica geral."

especialidades = [
    "dermatologia",
    "clinica_geral"
]


Exemplo:

"Quero cardiologia, dermatologia e clínica geral."

especialidades = [
    "cardiologia",
    "dermatologia",
    "clinica_geral"
]


--------------------------------------------------
CAMPO especialidade
--------------------------------------------------

O campo "especialidade" existe para compatibilidade
com o restante do sistema.

Quando houver especialidades na lista,
ele deve receber a PRIMEIRA especialidade.

Exemplo:

especialidades = [
    "dermatologia",
    "clinica_geral"
]

Então:

especialidade = dermatologia


--------------------------------------------------
SINÔNIMOS
--------------------------------------------------

Normalize:

"cardiologista"
"cardiologia"
"médico do coração"
"medico do coração"
"médico de coração"
"medico de coracao"

como:

cardiologia


Normalize:

"dermatologista"
"dermatologia"

como:

dermatologia


Normalize:

"clínica geral"
"clinica geral"
"clínico geral"
"clinico geral"

como:

clinica_geral


--------------------------------------------------
SEM ESPECIALIDADE
--------------------------------------------------

Quando nenhuma especialidade for mencionada:

especialidade = nao_informado

especialidades = []


IMPORTANTE:

Não invente especialidades.

Não repita especialidades na lista.


==================================================
4. DATA
==================================================

Extraia apenas a referência de data informada
pelo usuário.

Exemplos válidos:

hoje
amanhã
segunda
terça
quarta
quinta
sexta
sábado
domingo

Também podem existir datas específicas.

Exemplos:

"amanhã"

data = amanhã


"terça"

data = terça


"sexta-feira"

data = sexta-feira


Se nenhuma data for mencionada:

data = nao_informado


IMPORTANTE:

"manhã" NÃO é uma data.

"tarde" NÃO é uma data.

Essas palavras representam horário/período.


==================================================
5. HORÁRIO
==================================================

O campo "horario" representa a preferência
de horário.


--------------------------------------------------
HORÁRIO EXATO
--------------------------------------------------

Normalize horários específicos para HH:MM.

Exemplos:

"às 9"

horario = 09:00


"9 horas"

horario = 09:00


"às 10"

horario = 10:00


"14h"

horario = 14:00


"3 da tarde"

horario = 15:00


"5 da tarde"

horario = 17:00


--------------------------------------------------
MANHÃ
--------------------------------------------------

Normalize como:

manha

Exemplos:

"de manhã"

"pela manhã"

"no período da manhã"


--------------------------------------------------
TARDE
--------------------------------------------------

Normalize como:

tarde

Exemplos:

"à tarde"

"pela tarde"

"no período da tarde"

"depois do almoço"

"após o almoço"

"depois de almoçar"


--------------------------------------------------
SEM HORÁRIO
--------------------------------------------------

Quando nenhum horário ou período for informado:

horario = nao_informado


IMPORTANTE:

Se existir horário exato, ele tem prioridade.

Exemplo:

"3 da tarde"

horario = 15:00

NÃO use:

horario = tarde


==================================================
6. RESPOSTAS CURTAS
==================================================

Mesmo quando a mensagem parecer uma resposta curta
a uma pergunta anterior, extraia TODAS as informações
presentes nela.

Exemplo:

"terça às 10:00"

Resultado:

data = terça
horario = 10:00


Exemplo:

"quinta de manhã"

Resultado:

data = quinta
horario = manha


==================================================
7. MÚLTIPLAS CONSULTAS
==================================================

O usuário pode pedir:

1 especialidade
2 especialidades
3 especialidades


Exemplo:

"Quero dermatologia"

especialidade = dermatologia
especialidades = ["dermatologia"]


Exemplo:

"Quero dermatologia e clínica geral"

especialidade = dermatologia

especialidades = [
    "dermatologia",
    "clinica_geral"
]


Exemplo:

"Quero cardiologia e dermatologia terça"

especialidade = cardiologia

especialidades = [
    "cardiologia",
    "dermatologia"
]

data = terça


Exemplo:

"Quero cardiologia, dermatologia e clínica geral
terça às 10:00"

especialidade = cardiologia

especialidades = [
    "cardiologia",
    "dermatologia",
    "clinica_geral"
]

data = terça
horario = 10:00


==================================================
8. DADOS POR CONSULTA
==================================================

Além dos campos antigos, retorne também "consultas".

"consultas" deve ser SEMPRE uma lista. Cada item representa
uma especialidade mencionada e possui:
especialidade, data e horario.

Associe cada data e horário à especialidade correspondente.

Exemplo:
"Quero dermatologia terça às 10 e clínica geral quarta às 14"

consultas = [
    {{
        "especialidade": "dermatologia",
        "data": "terça",
        "horario": "10:00"
    }},
    {{
        "especialidade": "clinica_geral",
        "data": "quarta",
        "horario": "14:00"
    }}
]

Exemplo:
"Quero cardiologia segunda, dermatologia terça e clínica geral sexta"

consultas = [
    {{
        "especialidade": "cardiologia",
        "data": "segunda",
        "horario": "nao_informado"
    }},
    {{
        "especialidade": "dermatologia",
        "data": "terça",
        "horario": "nao_informado"
    }},
    {{
        "especialidade": "clinica_geral",
        "data": "sexta",
        "horario": "nao_informado"
    }}
]

Se uma informação for claramente comum a todas as especialidades,
ela pode ser repetida em cada item.

Se nenhuma especialidade for mencionada:
consultas = []

Nunca invente data ou horário.

Os campos "data" e "horario" antigos continuam existindo por
compatibilidade. Quando houver consultas, eles podem representar
os dados da primeira consulta.

==================================================
9. EXEMPLOS COMPLETOS
==================================================

Mensagem:

"Oi"

Resultado esperado:

{{
    "saudacao": true,
    "intencao": "nao_informado",
    "especialidade": "nao_informado",
    "especialidades": [],
    "data": "nao_informado",
    "horario": "nao_informado"
}}


Mensagem:

"Oi, quais especialidades vocês têm?"

Resultado esperado:

{{
    "saudacao": true,
    "intencao": "listar_especialidades",
    "especialidade": "nao_informado",
    "especialidades": [],
    "data": "nao_informado",
    "horario": "nao_informado"
}}


Mensagem:

"Vocês têm cardiologista?"

Resultado esperado:

{{
    "saudacao": false,
    "intencao": "consultar_medico",
    "especialidade": "cardiologia",
    "especialidades": [
        "cardiologia"
    ],
    "data": "nao_informado",
    "horario": "nao_informado"
}}


Mensagem:

"Tem médico de coração amanhã depois do almoço?"

Resultado esperado:

{{
    "saudacao": false,
    "intencao": "consultar",
    "especialidade": "cardiologia",
    "especialidades": [
        "cardiologia"
    ],
    "data": "amanhã",
    "horario": "tarde"
}}


Mensagem:

"Quero marcar cardiologista sexta às 17h"

Resultado esperado:

{{
    "saudacao": false,
    "intencao": "agendar",
    "especialidade": "cardiologia",
    "especialidades": [
        "cardiologia"
    ],
    "data": "sexta",
    "horario": "17:00"
}}


Mensagem:

"Quero marcar dermatologia e clínica geral"

Resultado esperado:

{{
    "saudacao": false,
    "intencao": "agendar",
    "especialidade": "dermatologia",
    "especialidades": [
        "dermatologia",
        "clinica_geral"
    ],
    "data": "nao_informado",
    "horario": "nao_informado"
}}


Mensagem:

"Quero cardiologia, dermatologia e clínica geral terça às 10:00"

Resultado esperado:

{{
    "saudacao": false,
    "intencao": "agendar",
    "especialidade": "cardiologia",
    "especialidades": [
        "cardiologia",
        "dermatologia",
        "clinica_geral"
    ],
    "data": "terça",
    "horario": "10:00"
}}


Mensagem:

"terça às 10:00"

Resultado esperado:

{{
    "saudacao": false,
    "intencao": "nao_informado",
    "especialidade": "nao_informado",
    "especialidades": [],
    "data": "terça",
    "horario": "10:00"
}}


Mensagem:

"Boa tarde, quero cancelar minha consulta."

Resultado esperado:

{{
    "saudacao": true,
    "intencao": "cancelar",
    "especialidade": "nao_informado",
    "especialidades": [],
    "data": "nao_informado",
    "horario": "nao_informado"
}}


==================================================
MENSAGEM DO USUÁRIO
==================================================

{mensagem}


Retorne somente os campos solicitados no formato
estruturado definido pelo sistema.
"""

    dados = {
        "model": MODELO,
        "prompt": prompt,

        "format": {
            "type": "object",

            "properties": {

                "saudacao": {
                    "type": "boolean"
                },

                "intencao": {
                    "type": "string",
                    "enum": [
                        "listar_especialidades",
                        "consultar_medico",
                        "consultar",
                        "agendar",
                        "remarcar",
                        "cancelar",
                        "nao_informado"
                    ]
                },

                "especialidade": {
                    "type": "string"
                },

                "especialidades": {
                    "type": "array",

                    "items": {
                        "type": "string",
                        "enum": [
                            "cardiologia",
                            "dermatologia",
                            "clinica_geral"
                        ]
                    },

                    "maxItems": 3,
                    "uniqueItems": True
                },

                "consultas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "especialidade": {
                                "type": "string",
                                "enum": [
                                    "cardiologia",
                                    "dermatologia",
                                    "clinica_geral"
                                ]
                            },
                            "data": {"type": "string"},
                            "horario": {"type": "string"}
                        },
                        "required": [
                            "especialidade",
                            "data",
                            "horario"
                        ]
                    },
                    "maxItems": 3
                },

                "data": {
                    "type": "string"
                },

                "horario": {
                    "type": "string"
                }
            },

            "required": [
                "saudacao",
                "intencao",
                "especialidade",
                "especialidades",
                "consultas",
                "data",
                "horario"
            ]
        },

        "stream": False
    }

    requisicao = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(
            dados
        ).encode(
            "utf-8"
        ),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {os.environ['OLLAMA_API_KEY']}"} if os.environ.get("OLLAMA_API_KEY") else {})
        },
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            requisicao, timeout=int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "90"))
        ) as resposta:

            resultado = json.loads(
                resposta.read().decode(
                    "utf-8"
                )
            )

    except Exception as erro:

        raise RuntimeError(
            "Não foi possível conectar ao Ollama. "
            f"Erro: {erro}"
        )

    # A API pode retornar um erro estruturado ou uma resposta vazia.
    # Nesse caso, usamos uma interpretação simples de contingência
    # para não interromper mensagens básicas, como "cardiologista".
    if isinstance(resultado, dict) and resultado.get("error"):
        resposta_modelo = ""
    else:
        resposta_modelo = resultado.get("response", "") if isinstance(resultado, dict) else ""

    resposta_modelo = resposta_modelo.strip() if isinstance(resposta_modelo, str) else ""

    # Alguns modelos podem envolver o JSON em cercas Markdown.
    if resposta_modelo.startswith("```"):
        resposta_modelo = re.sub(r"^```(?:json)?\s*|\s*```$", "", resposta_modelo, flags=re.IGNORECASE).strip()

    try:
        if not resposta_modelo:
            raise json.JSONDecodeError("Resposta vazia", resposta_modelo, 0)

        interpretacao = json.loads(resposta_modelo)

    except (json.JSONDecodeError, TypeError):
        interpretacao = _interpretacao_basica(mensagem)

    # ==================================================
    # NORMALIZAÇÃO DE SEGURANÇA
    # ==================================================

    if not isinstance(
        interpretacao,
        dict
    ):

        raise RuntimeError(
            "A interpretação retornada pelo Qwen "
            "não possui o formato esperado."
        )

    # --------------------------------------------------
    # SAUDAÇÃO
    # --------------------------------------------------

    if not isinstance(
        interpretacao.get("saudacao"),
        bool
    ):

        interpretacao[
            "saudacao"
        ] = False

    # --------------------------------------------------
    # INTENÇÃO
    # --------------------------------------------------

    intencoes_validas = [
        "listar_especialidades",
        "consultar_medico",
        "consultar",
        "agendar",
        "remarcar",
        "cancelar",
        "nao_informado"
    ]

    if (
        interpretacao.get(
            "intencao"
        )
        not in intencoes_validas
    ):

        interpretacao[
            "intencao"
        ] = "nao_informado"

    # --------------------------------------------------
    # ESPECIALIDADES
    # --------------------------------------------------

    especialidades_validas = [
        "cardiologia",
        "dermatologia",
        "clinica_geral"
    ]

    especialidades = interpretacao.get(
        "especialidades",
        []
    )

    if not isinstance(
        especialidades,
        list
    ):

        especialidades = []

    especialidades_normalizadas = []

    for especialidade in especialidades:

        if (
            especialidade
            in especialidades_validas
            and especialidade
            not in especialidades_normalizadas
        ):

            especialidades_normalizadas.append(
                especialidade
            )

    # Compatibilidade com o campo antigo.
    especialidade_principal = (
        interpretacao.get(
            "especialidade",
            "nao_informado"
        )
    )

    if (
        especialidade_principal
        in especialidades_validas
        and especialidade_principal
        not in especialidades_normalizadas
    ):

        especialidades_normalizadas.insert(
            0,
            especialidade_principal
        )

    # Máximo de três porque atualmente
    # a clínica possui três especialidades.
    especialidades_normalizadas = (
        especialidades_normalizadas[:3]
    )

    interpretacao[
        "especialidades"
    ] = especialidades_normalizadas

    if especialidades_normalizadas:

        interpretacao[
            "especialidade"
        ] = especialidades_normalizadas[0]

    else:

        interpretacao[
            "especialidade"
        ] = "nao_informado"

    # --------------------------------------------------
    # CONSULTAS INDIVIDUAIS
    # --------------------------------------------------

    consultas = interpretacao.get("consultas", [])

    if not isinstance(consultas, list):
        consultas = []

    consultas_normalizadas = []
    especialidades_em_consultas = set()

    for consulta in consultas[:3]:
        if not isinstance(consulta, dict):
            continue

        especialidade_consulta = consulta.get("especialidade")

        if (
            especialidade_consulta not in especialidades_validas
            or especialidade_consulta in especialidades_em_consultas
        ):
            continue

        data_consulta = consulta.get("data", "nao_informado")
        horario_consulta = consulta.get("horario", "nao_informado")

        if not isinstance(data_consulta, str) or not data_consulta.strip():
            data_consulta = "nao_informado"

        if not isinstance(horario_consulta, str) or not horario_consulta.strip():
            horario_consulta = "nao_informado"

        consultas_normalizadas.append({
            "especialidade": especialidade_consulta,
            "data": data_consulta.strip(),
            "horario": horario_consulta.strip()
        })

        especialidades_em_consultas.add(especialidade_consulta)

    for especialidade in especialidades_normalizadas:
        if especialidade not in especialidades_em_consultas:
            consultas_normalizadas.append({
                "especialidade": especialidade,
                "data": "nao_informado",
                "horario": "nao_informado"
            })

    interpretacao["consultas"] = consultas_normalizadas[:3]

    # --------------------------------------------------
    # DATA
    # --------------------------------------------------

    data = interpretacao.get(
        "data",
        "nao_informado"
    )

    if not isinstance(
        data,
        str
    ):

        data = "nao_informado"

    if not data.strip():

        data = "nao_informado"

    interpretacao[
        "data"
    ] = data.strip()

    # --------------------------------------------------
    # HORÁRIO
    # --------------------------------------------------

    horario = interpretacao.get(
        "horario",
        "nao_informado"
    )

    if not isinstance(
        horario,
        str
    ):

        horario = "nao_informado"

    if not horario.strip():

        horario = "nao_informado"

    interpretacao[
        "horario"
    ] = horario.strip()

    return interpretacao


# ==================================================
# TESTE MANUAL
# ==================================================

if __name__ == "__main__":

    mensagem = input(
        "Você: "
    ).strip()

    if not mensagem:

        print(
            "Digite uma mensagem para testar."
        )

    else:

        print(
            "\nBartho está interpretando...\n"
        )

        interpretacao = (
            interpretar_mensagem(
                mensagem
            )
        )

        print(
            "=== INTERPRETAÇÃO ==="
        )

        print(
            json.dumps(
                interpretacao,
                indent=4,
                ensure_ascii=False
            )
        )
