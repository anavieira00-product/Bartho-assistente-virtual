import time
import re
import json
import urllib.request

from teste_llm import interpretar_mensagem

from validador import (
    validar_horario,
    validar_intencao
)

from calendario import (
    resolver_data,
    consultorio_aberto
)

from agenda import (
    criar_banco,
    cadastrar_medicos,
    cadastrar_dias_atendimento,
    verificar_atendimento,
    consultar_horarios_livres,
    criar_agendamento,
    buscar_agendamentos,
    cancelar_agendamento,
    remarcar_agendamento,
    consultar_dias_medico,
    consultar_especialidades,
    consultar_medicos_por_especialidade,
    registrar_atendimento_humano
)

from conversa import EstadoConversa

from agente import decidir_tool

from rag import buscar_contexto

from metricas import (
    iniciar_sessao,
    registrar_interacao,
    registrar_qwen,
    registrar_rag,
    registrar_tool,
    registrar_resolucao,
    registrar_handoff,
    registrar_erro,
    finalizar_sessao,
)


# Contexto conversacional simples para perguntas informativas.
contexto_conversa = {
    "assunto_atual": None,
    "especialidade_atual": None,
    "plano_atual": None,
    "pergunta_anterior": None,
    "resposta_anterior": None,
    "pendencia": None,
    "aguardando_mais_alguma_coisa": False,
    "saudacao_realizada": False,
    "ultima_consulta": None,
    "consultas_sessao": []
}




class PerguntaInterativaWeb(Exception):
    """Sinaliza que o fluxo precisa aguardar uma resposta enviada pela interface web."""
    def __init__(self, pergunta):
        self.pergunta = str(pergunta or "").strip()
        super().__init__(self.pergunta)


def ler_entrada_usuario(prompt=""):
    """Mantém input no terminal e evita bloqueio de stdin quando usado pela API."""
    import os
    if os.getenv("BARTHO_MODO_WEB", "").strip().lower() in {"1", "true", "sim"}:
        pergunta = str(prompt or "").replace("Bartho:", "").replace("Você:", "").strip()
        raise PerguntaInterativaWeb(pergunta or "Como posso ajudar?")
    return input(prompt)


# ==================================================
# FUNÇÕES AUXILIARES
# ==================================================

def normalizar_horario(texto):
    texto = texto.lower().strip()

    if texto.endswith("h"):
        numero = texto[:-1].strip()

        if numero.isdigit():
            return numero.zfill(2) + ":00"

    if ":" not in texto and texto.isdigit():
        return texto.zfill(2) + ":00"

    return texto


def nome_especialidade(especialidade):
    nomes = {
        "cardiologia": "Cardiologia",
        "dermatologia": "Dermatologia",
        "clinica_geral": "Clínica Geral"
    }

    return nomes.get(
        especialidade,
        especialidade.replace("_", " ").title()
    )


def _texto_consulta(consulta):
    return normalizar_mensagem_curta(
        f"{consulta[1]} {consulta[2]} {consulta[3]} {consulta[4]}"
    )


def _indices_por_escolha_natural(escolha, consultas):
    """Resolve número, ordinal, especialidade, médico ou conjunto sem LLM."""
    texto = normalizar_mensagem_curta(escolha)
    if not texto:
        return []

    # "todas", "as três", "as duas".
    if any(x in texto for x in ("todas", "todos", "as tres", "os tres", "as duas", "os dois")):
        return list(range(len(consultas)))

    encontrados = []

    # Números isolados: "1", "1 e 3", "consulta 2".
    for numero in re.findall(r"(?<!\d)(\d+)(?!\d)", texto):
        indice = int(numero) - 1
        if 0 <= indice < len(consultas) and indice not in encontrados:
            encontrados.append(indice)

    ordinais = {
        "primeira": 0, "primeiro": 0,
        "segunda": 1, "segundo": 1,
        "terceira": 2, "terceiro": 2,
    }
    for palavra, indice in ordinais.items():
        if palavra in texto and 0 <= indice < len(consultas) and indice not in encontrados:
            encontrados.append(indice)

    # Especialidade e médico. Usa o conteúdo real retornado pelo banco.
    aliases = {
        "cardiologia": ("cardiologia", "cardiologista", "coracao"),
        "dermatologia": ("dermatologia", "dermatologista", "pele"),
        "clinica_geral": ("clinica geral", "clinico geral", "clinica_geral"),
    }
    for indice, consulta in enumerate(consultas):
        medico = normalizar_mensagem_curta(str(consulta[1]))
        especialidade = normalizar_mensagem_curta(str(consulta[2]))
        nome_medico = medico.replace("dr ", "").replace("dra ", "").strip()
        sobrenome_ou_nome = [p for p in nome_medico.split() if len(p) >= 3]

        corresponde = False
        for chave, termos in aliases.items():
            if chave.replace("_", " ") in especialidade and any(t in texto for t in termos):
                corresponde = True
                break
        if medico and medico in texto:
            corresponde = True
        elif any(p in texto for p in sobrenome_ou_nome):
            corresponde = True

        if corresponde and indice not in encontrados:
            encontrados.append(indice)

    return sorted(encontrados)


def selecionar_consultas(consultas, permitir_multiplas=False, escolha_inicial=None):
    if not consultas:
        return [], ""
    if len(consultas) == 1:
        return [consultas[0]], escolha_inicial or ""

    print("\nBartho: Encontrei mais de uma consulta:")
    for numero, consulta in enumerate(consultas, start=1):
        print(
            f"{numero} - {consulta[1]} | {consulta[2]} | "
            f"{consulta[3]} | {consulta[4]}"
        )

    tentativa = escolha_inicial
    while True:
        if tentativa is None:
            tentativa = perguntar_preservando_fluxo(
                "Qual consulta você deseja?",
                assunto_pendente="escolha_consulta"
            )

        indices = _indices_por_escolha_natural(tentativa, consultas)
        if indices:
            if not permitir_multiplas and len(indices) > 1:
                print("Bartho: Para remarcar, escolha uma consulta por vez.")
            else:
                return [consultas[i] for i in indices], tentativa

        print(
            "Bartho: Você pode escolher pelo número, especialidade, médico "
            "ou posição, por exemplo: '1', 'a de cardiologia' ou 'a primeira'."
        )
        tentativa = None


def escolher_consulta(consultas):
    """Compatibilidade com fluxos antigos que esperam uma única consulta."""
    selecionadas, _ = selecionar_consultas(consultas, permitir_multiplas=False)
    return selecionadas[0] if selecionadas else None

def nomes_dias(dias):
    mapa = {
        0: "segunda",
        1: "terça",
        2: "quarta",
        3: "quinta",
        4: "sexta",
        5: "sábado",
        6: "domingo"
    }

    return [
        mapa[dia]
        for dia in dias
        if dia in mapa
    ]


def formatar_lista_dias(dias):
    if not dias:
        return ""

    if len(dias) == 1:
        return dias[0]

    if len(dias) == 2:
        return f"{dias[0]} e {dias[1]}"

    return (
        ", ".join(dias[:-1])
        + " e "
        + dias[-1]
    )


def extrair_especialidades_explicitas(mensagem):
    """Extrai especialidades claramente citadas sem depender do LLM."""
    texto = normalizar_mensagem_curta(mensagem)
    encontradas = []

    grupos = (
        ("cardiologia", ("cardiologia", "cardiologista", "cardio", "medico de coracao")),
        ("dermatologia", ("dermatologia", "dermatologista", "dermato")),
        ("clinica_geral", ("clinica geral", "clinico geral", "clinica_geral")),
    )

    for especialidade, termos in grupos:
        if any(termo in texto for termo in termos):
            encontradas.append(especialidade)

    # Continuação contextual depois de a clínica listar as três especialidades.
    referencia_todas = any(
        termo in texto
        for termo in ("as tres", "os tres", "todas as especialidades", "as 3", "os 3")
    )
    if referencia_todas and contexto_conversa.get("assunto_atual") == "especialidades":
        return ["cardiologia", "dermatologia", "clinica_geral"]

    return encontradas



def extrair_datas_multiplas_explicitas(mensagem):
    """Associa datas/dias às especialidades citadas na mesma mensagem.

    Não tenta adivinhar relações implícitas: só usa o trecho que acompanha
    cada especialidade explicitamente mencionada. O Qwen continua como
    fallback para formulações realmente ambíguas.
    """
    import re

    texto = normalizar_mensagem_curta(mensagem)
    aliases = (
        ("cardiologia", r"\b(?:cardiologia|cardiologista|cardio|medico de coracao)\b"),
        ("dermatologia", r"\b(?:dermatologia|dermatologista|dermato)\b"),
        ("clinica_geral", r"\b(?:clinica geral|clinico geral|clinica_geral)\b"),
    )

    ocorrencias = []
    for especialidade, padrao in aliases:
        for match in re.finditer(padrao, texto):
            ocorrencias.append((match.start(), match.end(), especialidade))
    ocorrencias.sort()

    if not ocorrencias:
        return {}

    dias = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo", "hoje", "amanha")
    resultado = {}

    for indice, (_, fim, especialidade) in enumerate(ocorrencias):
        proximo_inicio = ocorrencias[indice + 1][0] if indice + 1 < len(ocorrencias) else len(texto)
        trecho = texto[fim:proximo_inicio]

        encontrado = None
        for dia in dias:
            if re.search(rf"\b{re.escape(dia)}\b", trecho):
                encontrado = dia
                break

        if not encontrado:
            data_match = re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", trecho)
            if data_match:
                encontrado = data_match.group(0)

        if encontrado:
            resultado[especialidade] = encontrado

    return resultado


def pergunta_dias_medicos_multiplos(mensagem):
    """Detecta pergunta sobre dias no contexto de uma operação com várias consultas.

    A decisão não depende da frase exata "quais dias eles atendem". O contexto
    da operação já informa que há vários médicos; basta a mensagem ser uma
    pergunta sobre dias e não restringir explicitamente a uma especialidade.
    """
    texto = normalizar_mensagem_curta(mensagem)
    pergunta_dias = any(termo in texto for termo in (
        "quais dias", "que dias", "qual dia", "quando atende", "quando atendem",
        "dias atende", "dias atendem", "dias de atendimento"
    ))
    return pergunta_dias and not extrair_especialidade_texto(mensagem)


def responder_pergunta_lateral_multiplos(mensagem, consultas, assunto_pendente):
    """Resolve perguntas laterais sem perder o conjunto em andamento."""
    if not consultas:
        return False

    # Pergunta coletiva sobre os médicos/especialidades da própria operação.
    if pergunta_dias_medicos_multiplos(mensagem):
        print("\nBartho: Estes são os dias de atendimento:")
        for consulta in consultas:
            dias_texto = formatar_lista_dias(nomes_dias(consulta["dias"]))
            print(
                f"- {consulta['medico']} ({nome_especialidade(consulta['especialidade'])}): "
                f"{dias_texto}."
            )
        return True

    # FAQs conhecidas podem ser respondidas sem chamar o Qwen e, ao terminar,
    # o while de perguntar_preservando_fluxo volta exatamente à pendência atual.
    if responder_convenio_e_servicos(mensagem):
        return True
    if responder_servico_nao_oferecido(mensagem):
        return True
    if responder_informacao_clinica_rapida(mensagem):
        return True
    if responder_valor_consulta(mensagem):
        return True
    if pergunta_sobre_documentos_ou_preparo(mensagem):
        if responder_documentos_consulta(mensagem, {"especialidade": "nao_informado"}):
            return True
    if responder_administrativo_sem_inventar(mensagem):
        return True

    return False

def interpretar_e_corrigir(mensagem):
    interpretacao = interpretar_mensagem(mensagem)

    interpretacao["intencao"] = validar_intencao(
        mensagem,
        interpretacao["intencao"]
    )

    interpretacao["horario"] = validar_horario(
        mensagem,
        interpretacao["horario"]
    )

    # O LLM continua interpretando linguagem natural, mas entidades que o
    # usuário escreveu explicitamente não podem ser perdidas pela geração.
    explicitas = extrair_especialidades_explicitas(mensagem)
    atuais = interpretacao.get("especialidades", [])
    if not isinstance(atuais, list):
        atuais = []

    combinadas = []
    for item in explicitas + atuais:
        if item in {"cardiologia", "dermatologia", "clinica_geral"} and item not in combinadas:
            combinadas.append(item)

    if combinadas:
        interpretacao["especialidades"] = combinadas[:3]
        if len(combinadas) == 1:
            interpretacao["especialidade"] = combinadas[0]
        elif interpretacao.get("especialidade") not in combinadas:
            interpretacao["especialidade"] = combinadas[0]

    return interpretacao


def mostrar_interpretacao(interpretacao):
    print("\n=== INTERPRETAÇÃO DO QWEN ===")
    print(
        "Saudação:",
        interpretacao.get("saudacao", False)
    )
    print(
        "Intenção:",
        interpretacao["intencao"]
    )
    print(
        "Especialidade:",
        interpretacao["especialidade"]
    )
    especialidades = interpretacao.get("especialidades", [])
    if len(especialidades) > 1:
        print(
            "Especialidades:",
            ", ".join(especialidades)
        )
    print(
        "Data:",
        interpretacao["data"]
    )
    print(
        "Horário:",
        interpretacao["horario"]
    )


def registrar_ultima_consulta(
    paciente,
    especialidade,
    medico,
    data,
    horario,
    agendamento_id=None
):
    contexto_conversa["ultima_consulta"] = {
        "paciente": paciente,
        "especialidade": especialidade,
        "medico": medico,
        "data": data,
        "horario": horario,
        "agendamento_id": agendamento_id
    }

    # A consulta confirmada também passa a ser o contexto clínico atual.
    contexto_conversa["especialidade_atual"] = especialidade


def especialidade_da_sessao():
    consulta = contexto_conversa.get("ultima_consulta")

    if consulta:
        return consulta.get("especialidade")

    return contexto_conversa.get("especialidade_atual")


def registrar_consultas_sessao(consultas):
    """Guarda o conjunto confirmado nesta sessão sem substituir pela última."""
    contexto_conversa["consultas_sessao"] = [dict(c) for c in consultas]
    if consultas:
        contexto_conversa["ultima_consulta"] = dict(consultas[-1])
        contexto_conversa["especialidade_atual"] = consultas[-1].get("especialidade")


def remover_consulta_da_sessao(agendamento_id=None, paciente=None, medico=None, data=None, horario=None):
    mantidas = []
    for c in contexto_conversa.get("consultas_sessao", []):
        mesma = False
        if agendamento_id is not None and c.get("agendamento_id") == agendamento_id:
            mesma = True
        elif all((paciente, medico, data, horario)):
            mesma = (
                c.get("paciente") == paciente and c.get("medico") == medico
                and c.get("data") == data and c.get("horario") == horario
            )
        if not mesma:
            mantidas.append(c)
    contexto_conversa["consultas_sessao"] = mantidas
    contexto_conversa["ultima_consulta"] = mantidas[-1] if mantidas else None


def atualizar_consulta_da_sessao(agendamento_id, paciente, especialidade, medico, data, horario):
    nova = {
        "paciente": paciente, "especialidade": especialidade, "medico": medico,
        "data": data, "horario": horario, "agendamento_id": agendamento_id
    }
    lista = contexto_conversa.get("consultas_sessao", [])
    atualizou = False
    for i, c in enumerate(lista):
        if c.get("agendamento_id") == agendamento_id:
            lista[i] = nova
            atualizou = True
            break
    if not atualizou:
        lista.append(nova)
    contexto_conversa["consultas_sessao"] = lista
    contexto_conversa["ultima_consulta"] = nova
    contexto_conversa["especialidade_atual"] = especialidade


def marcar_operacao_concluida():
    """
    Uma operação concluída encerra aquele fluxo, não necessariamente
    todo o atendimento. A próxima entrada pode ser outro pedido ou
    uma resposta curta à pergunta 'Posso ajudar...?'.
    """
    contexto_conversa["aguardando_mais_alguma_coisa"] = True
    print("\nBartho: Posso ajudar com mais alguma coisa?")


# ==================================================
# FINALIZAR AGENDAMENTO
# ==================================================

def finalizar_agendamento(
    especialidade,
    medico,
    data_banco,
    horario,
    paciente=None
):
    if not paciente:
        paciente = perguntar_preservando_fluxo(
            "Qual é o nome do paciente?",
            assunto_pendente="agendamento_paciente",
            especialidade=especialidade
        )

    if not paciente:
        print(
            "\nBartho: Preciso do nome do paciente "
            "para realizar o agendamento."
        )
        return None

    print("\n=== RESUMO DO AGENDAMENTO ===")
    print("Paciente:", paciente)
    print(
        "Especialidade:",
        nome_especialidade(especialidade)
    )
    print("Médico:", medico)
    print("Data:", data_banco)
    print("Horário:", horario)

    confirmacao = perguntar_preservando_fluxo(
        "Deseja confirmar esse agendamento?",
        assunto_pendente="agendamento_confirmacao",
        especialidade=especialidade
    ).lower().strip()

    if confirmacao not in [
        "sim",
        "s",
        "yes"
    ]:
        print(
            "\nBartho: Agendamento não confirmado."
        )
        return paciente

    sucesso = criar_agendamento(
        paciente,
        medico,
        data_banco,
        horario
    )

    if sucesso:
        print(
            "\nBartho: Consulta agendada "
            "com sucesso!"
        )
        print(
            f"{paciente} | "
            f"{medico} | "
            f"{data_banco} | "
            f"{horario}"
        )

        registrar_ultima_consulta(
            paciente,
            especialidade,
            medico,
            data_banco,
            horario
        )
        registrar_resolucao("agendamento")
        marcar_operacao_concluida()
    else:
        print(
            "\nBartho: Não foi possível realizar "
            "o agendamento."
        )
        print(
            "Bartho: O horário pode ter sido "
            "ocupado."
        )

    return paciente

# ==================================================
# AGENDAMENTO CONVERSACIONAL
# ==================================================

# ==================================================
# CONTEXTO DURANTE UMA OPERAÇÃO
# ==================================================

def tratar_pergunta_lateral(mensagem, assunto_pendente=None, especialidade=None):
    """
    Responde uma pergunta lateral sem apagar a operação em andamento.

    A mensagem só pode ser desviada depois de verificarmos se ela já é
    uma resposta válida para o dado que o fluxo está aguardando.
    """

    texto = normalizar_mensagem_curta(mensagem)

    # Confirmações/negações pertencem ao passo atual, não são perguntas laterais.
    respostas_confirmacao = {
        "sim", "s", "yes", "confirmo", "confirmar", "pode",
        "pode sim", "pode ser", "quero", "claro",
        "nao", "não", "n", "cancelar", "desistir"
    }

    if assunto_pendente and (
        "confirmacao" in assunto_pendente
        or "deseja_agendar" in assunto_pendente
    ):
        if texto in respostas_confirmacao:
            return False

    # Escolha numérica de uma consulta.
    if assunto_pendente == "escolha_consulta" and texto.isdigit():
        return False

    # Nome do paciente: texto declarativo é tratado como dado.
    if assunto_pendente and "paciente" in assunto_pendente:
        marcadores_pergunta = (
            "qual ", "quais ", "quem ", "quando ", "onde ",
            "como ", "porque ", "por que ", "vocês ", "voces ",
            "tem ", "têm ", "aceita ", "atende ", "posso ",
            "preciso ", "documento ", "convenio ", "convênio "
        )
        if "?" not in mensagem and not texto.startswith(marcadores_pergunta):
            return False

    # Respostas diretas ao dado aguardado não precisam do LLM.
    if assunto_pendente and ("horario" in assunto_pendente or "periodo" in assunto_pendente):
        if normalizar_horario(texto) != texto or texto.isdigit() or texto in {"manha", "tarde", "qualquer", "tanto faz"}:
            return False

    if assunto_pendente and "especialidade" in assunto_pendente:
        if extrair_especialidade_texto(mensagem):
            return False

    if assunto_pendente and "data" in assunto_pendente:
        try:
            if resolver_data(mensagem) is not None:
                return False
        except Exception:
            pass

    # FAQs objetivas têm fonte própria e não precisam de duas interpretações
    # sequenciais (operacional + contextual).
    if responder_convenio_e_servicos(mensagem):
        return True
    if responder_servico_nao_oferecido(mensagem):
        return True
    if responder_informacao_clinica_rapida(mensagem):
        return True
    if responder_valor_consulta(mensagem):
        return True
    if pergunta_sobre_documentos_ou_preparo(mensagem):
        if responder_documentos_consulta(
            mensagem,
            {"especialidade": especialidade or "nao_informado"}
        ):
            return True
    if responder_administrativo_sem_inventar(mensagem):
        return True

    # Somente o que realmente depende de semântica/contexto chega ao Qwen.
    decisao = interpretar_turno_contextual(mensagem)

    if not decisao:
        return False

    tipo = decisao.get("tipo")
    objetivo = decisao.get("objetivo")
    especialidade_decisao = (
        decisao.get("especialidade")
        or especialidade
        or especialidade_da_sessao()
    )

    if objetivo == "atendimento_humano":
        tratar_pedido_atendimento_humano(mensagem)
        return True

    if tipo == "encerrar":
        print("\nBartho: Tudo bem! Até mais! 👋")
        return True

    if objetivo == "especialidades":
        especialidades = consultar_especialidades()

        if especialidades:
            resposta = (
                "Atualmente temos "
                + ", ".join(
                    nome_especialidade(item)
                    for item in especialidades
                )
                + "."
            )
            print(f"\nBartho: {resposta}")
            registrar_contexto("especialidades", mensagem, resposta)
            return True

    if objetivo == "convenios":
        return responder_pergunta_convenio(mensagem)

    if objetivo == "documentos_preparo":
        return responder_documentos_consulta(
            mensagem,
            {"especialidade": especialidade_decisao or "nao_informado"}
        )

    if objetivo == "consultar_dias" and especialidade_decisao:
        return responder_dias_especialidade(especialidade_decisao)

    if tipo == "pergunta_informativa":
        return responder_informacao_contextual(mensagem, decisao)

    return False


def perguntar_preservando_fluxo(pergunta, assunto_pendente, especialidade=None, consultas_multiplas=None):
    """Mantém a pendência original enquanto responde perguntas laterais."""
    while True:
        contexto_conversa["assunto_atual"] = assunto_pendente
        contexto_conversa["especialidade_atual"] = especialidade
        contexto_conversa["pendencia"] = assunto_pendente

        resposta = ler_entrada_usuario(f"\nBartho: {pergunta}\nVocê: ").strip()

        if not resposta:
            continue

        if consultas_multiplas and responder_pergunta_lateral_multiplos(
            resposta, consultas_multiplas, assunto_pendente
        ):
            continue

        # Em campos agrupados, uma resposta que cita especialidades junto com
        # dias/horários é dado da operação, não uma pergunta lateral.
        if consultas_multiplas and assunto_pendente in {"multiplos_datas", "multiplos_horarios"}:
            if extrair_especialidades_explicitas(resposta):
                return resposta

        if tratar_pergunta_lateral(
            resposta,
            assunto_pendente=assunto_pendente,
            especialidade=especialidade
        ):
            continue

        return resposta


def fluxo_agendamento(
    mensagem_inicial,
    interpretacao_inicial,
    paciente=None
):
    estado = EstadoConversa()
    estado.atualizar(
        interpretacao_inicial
    )

    # ----------------------------------------------
    # ESPECIALIDADE
    # ----------------------------------------------

    while not estado.especialidade:

        resposta = perguntar_preservando_fluxo(
            "Qual especialidade você procura?",
            assunto_pendente="agendamento_especialidade"
        )

        interpretacao = interpretar_e_corrigir(
            resposta
        )

        interpretacao["intencao"] = "agendar"

        estado.atualizar(
            interpretacao
        )

    # ----------------------------------------------
    # DATA
    # ----------------------------------------------

    data_resolvida = None
    medico = None

    while data_resolvida is None:

        if not estado.data:

            resposta = perguntar_preservando_fluxo(
                "Para qual dia você gostaria de agendar?",
                assunto_pendente="agendamento_data",
                especialidade=estado.especialidade
            )

            # Antes de tratar a mensagem como uma data, interpretamos o turno
            # dentro do contexto do agendamento.
            contexto_conversa["assunto_atual"] = "agendamento"
            contexto_conversa["especialidade_atual"] = estado.especialidade
            contexto_conversa["pendencia"] = "data"

            interpretacao = interpretar_e_corrigir(
                resposta
            )

            interpretacao["intencao"] = "agendar"

            estado.atualizar(
                interpretacao
            )

            # Nunca enviamos None para resolver_data().
            if not estado.data:
                print(
                    "\nBartho: Para continuar o agendamento, "
                    "me diga um dia ou uma data."
                )
                continue

        if not estado.data:
            continue

        data_tentativa = resolver_data(
            estado.data
        )

        if data_tentativa is None:
            print(
                "\nBartho: Não consegui "
                "interpretar essa data."
            )
            estado.data = None
            continue

        if not consultorio_aberto(
            data_tentativa
        ):
            print(
                "\nBartho: O consultório não abre "
                "nessa data."
            )
            estado.data = None
            continue

        dia_semana = data_tentativa.weekday()

        medicos = verificar_atendimento(
            estado.especialidade,
            dia_semana
        )

        if not medicos:

            medicos_especialidade = (
                consultar_medicos_por_especialidade(
                    estado.especialidade
                )
            )

            print(
                f"\nBartho: Não há atendimento de "
                f"{nome_especialidade(estado.especialidade)} "
                f"nesse dia."
            )

            if medicos_especialidade:
                medico_temp = (
                    medicos_especialidade[0]
                )

                dias = consultar_dias_medico(
                    medico_temp
                )

                dias_texto = formatar_lista_dias(
                    nomes_dias(dias)
                )

                print(
                    f"Bartho: {medico_temp} atende "
                    f"às {dias_texto}s."
                )

            estado.data = None
            continue

        medico = medicos[0][0]
        data_resolvida = data_tentativa

    data_banco = data_resolvida.strftime(
        "%d/%m/%Y"
    )

    # ----------------------------------------------
    # HORÁRIOS
    # ----------------------------------------------

    horarios_livres = consultar_horarios_livres(
        medico,
        data_banco
    )

    if not horarios_livres:
        print(
            "\nBartho: Não existem horários "
            "disponíveis nessa data."
        )
        return

    horario_escolhido = None

    while horario_escolhido is None:

        if estado.horario == "manha":
            horarios_possiveis = [
                h
                for h in horarios_livres
                if 8 <= int(h[:2]) < 12
            ]

        elif estado.horario == "tarde":
            horarios_possiveis = [
                h
                for h in horarios_livres
                if 12 <= int(h[:2]) <= 17
            ]

        elif (
            estado.horario
            and estado.horario != "nao_informado"
        ):
            horario_normalizado = (
                normalizar_horario(
                    estado.horario
                )
            )

            if (
                horario_normalizado
                in horarios_livres
            ):
                horario_escolhido = (
                    horario_normalizado
                )
                break

            horarios_possiveis = []

        else:
            horarios_possiveis = (
                horarios_livres
            )

        if not horarios_possiveis:
            print(
                "\nBartho: Não há horário "
                "disponível para essa preferência."
            )
            estado.horario = None
            continue

        print(
            f"\nBartho: {medico} tem os "
            f"seguintes horários disponíveis "
            f"em {data_banco}:"
        )

        print(
            ", ".join(horarios_possiveis)
        )

        resposta = perguntar_preservando_fluxo(
            "Qual horário você prefere?",
            assunto_pendente="agendamento_horario",
            especialidade=estado.especialidade
        )

        horario_tentativa = (
            normalizar_horario(resposta)
        )

        if (
            horario_tentativa
            in horarios_possiveis
        ):
            horario_escolhido = (
                horario_tentativa
            )
            estado.horario = (
                horario_tentativa
            )

        else:
            interpretacao = (
                interpretar_e_corrigir(
                    resposta
                )
            )

            horario_interpretado = (
                interpretacao["horario"]
            )

            if horario_interpretado not in [
                None,
                "nao_informado",
                "manha",
                "tarde"
            ]:
                horario_interpretado = (
                    normalizar_horario(
                        horario_interpretado
                    )
                )

                if (
                    horario_interpretado
                    in horarios_possiveis
                ):
                    horario_escolhido = (
                        horario_interpretado
                    )
                    estado.horario = (
                        horario_interpretado
                    )
                    continue

            print(
                "\nBartho: Esse horário não está "
                "disponível."
            )

    paciente = finalizar_agendamento(
        estado.especialidade,
        medico,
        data_banco,
        horario_escolhido,
        paciente
    )

    return paciente


# ==================================================
# AGENDAMENTO DE MÚLTIPLAS ESPECIALIDADES
# ==================================================

def fluxo_agendamentos_multiplos(
    mensagem_inicial,
    interpretacao_inicial
):
    """Agendamento agrupado de duas ou três especialidades."""

    especialidades = interpretacao_inicial.get("especialidades", [])

    if not isinstance(especialidades, list):
        especialidades = []

    validas = []
    for item in especialidades:
        if (
            item in ["cardiologia", "dermatologia", "clinica_geral"]
            and item not in validas
        ):
            validas.append(item)

    especialidades = validas[:3]

    if len(especialidades) <= 1:
        return fluxo_agendamento(
            mensagem_inicial,
            interpretacao_inicial
        )

    # Dados individuais que o Qwen já conseguiu extrair da mensagem inicial.
    dados_iniciais = {}
    for item in interpretacao_inicial.get("consultas", []):
        if isinstance(item, dict):
            esp = item.get("especialidade")
            if esp in especialidades:
                dados_iniciais[esp] = item

    print("\nBartho: Claro. Vamos organizar as consultas juntas.")
    print("\nBartho: Você quer agendar:")

    consultas = []

    for indice, especialidade in enumerate(especialidades, start=1):
        medicos = consultar_medicos_por_especialidade(especialidade)

        if not medicos:
            print(
                f"{indice}. {nome_especialidade(especialidade)} — "
                "sem médico cadastrado no momento."
            )
            continue

        medico = medicos[0]
        dias = consultar_dias_medico(medico)
        dias_texto = formatar_lista_dias(nomes_dias(dias))
        inicial = dados_iniciais.get(especialidade, {})

        print(
            f"{indice}. {nome_especialidade(especialidade)} — "
            f"{medico} — atende às {dias_texto}s"
        )

        consultas.append({
            "especialidade": especialidade,
            "medico": medico,
            "dias": dias,
            "data_texto": inicial.get("data", "nao_informado"),
            "data": None,
            "data_banco": None,
            "horario": inicial.get("horario", "nao_informado"),
            "horarios_livres": []
        })

    if len(consultas) <= 1:
        print(
            "\nBartho: Não consegui montar um agendamento múltiplo "
            "com as especialidades informadas."
        )
        return None

    def aplicar_dados_interpretados(interpretacao):
        itens = interpretacao.get("consultas", [])
        if not isinstance(itens, list):
            return

        for item in itens:
            if not isinstance(item, dict):
                continue

            esp = item.get("especialidade")
            consulta = next(
                (c for c in consultas if c["especialidade"] == esp),
                None
            )

            if not consulta:
                continue

            data = item.get("data", "nao_informado")
            horario = item.get("horario", "nao_informado")

            if (
                consulta.get("data") is None
                and isinstance(data, str)
                and data.strip()
                and data != "nao_informado"
            ):
                consulta["data_texto"] = data.strip()

            if (
                isinstance(horario, str)
                and horario.strip()
                and horario != "nao_informado"
            ):
                consulta["horario"] = horario.strip()

    def validar_data_consulta(consulta):
        texto = consulta.get("data_texto")

        if not texto or texto == "nao_informado":
            return False

        data_tentativa = resolver_data(texto)

        if data_tentativa is None:
            return False

        if not consultorio_aberto(data_tentativa):
            return False

        if data_tentativa.weekday() not in consulta["dias"]:
            return False

        consulta["data"] = data_tentativa
        consulta["data_banco"] = data_tentativa.strftime("%d/%m/%Y")
        return True

    # Aproveita datas/horários já presentes na primeira mensagem.
    for consulta in consultas:
        validar_data_consulta(consulta)

    # --------------------------------------------------
    # DATAS AGRUPADAS
    # --------------------------------------------------
    while any(c["data"] is None for c in consultas):
        faltantes = [c for c in consultas if c["data"] is None]

        print("\nBartho: Quais dias você prefere para cada consulta?")
        for c in faltantes:
            print(
                f"- {nome_especialidade(c['especialidade'])} — "
                f"{c['medico']}"
            )

        resposta = perguntar_preservando_fluxo(
            "Informe os dias para cada consulta.",
            assunto_pendente="multiplos_datas",
            consultas_multiplas=consultas
        )

        # Primeiro preservamos associações explícitas sem depender do LLM.
        datas_explicitas = extrair_datas_multiplas_explicitas(resposta)
        for especialidade, data_texto in datas_explicitas.items():
            consulta = next(
                (c for c in consultas if c["especialidade"] == especialidade),
                None
            )
            if consulta and consulta["data"] is None:
                consulta["data_texto"] = data_texto

        # O Qwen fica como fallback apenas se ainda houver consulta sem data
        # e a mensagem não tiver associado todas as faltantes explicitamente.
        faltantes_sem_associacao = [
            c for c in faltantes
            if c["especialidade"] not in datas_explicitas
        ]
        if faltantes_sem_associacao:
            interpretacao = interpretar_e_corrigir(resposta)
            aplicar_dados_interpretados(interpretacao)

        houve_progresso = False

        for consulta in faltantes:
            if validar_data_consulta(consulta):
                houve_progresso = True
                print(
                    f"Bartho: {nome_especialidade(consulta['especialidade'])} "
                    f"ficou para {consulta['data_banco']}."
                )

        # Explica somente as escolhas que foram entendidas, mas são inválidas.
        houve_escolha_invalida = False
        for consulta in consultas:
            if consulta["data"] is not None:
                continue

            texto = consulta.get("data_texto")
            if texto and texto != "nao_informado":
                houve_escolha_invalida = True
                data_tentativa = resolver_data(texto)
                dias_texto = formatar_lista_dias(nomes_dias(consulta["dias"]))

                if data_tentativa is None:
                    print(
                        f"Bartho: Não consegui interpretar a data de "
                        f"{nome_especialidade(consulta['especialidade'])}."
                    )
                elif not consultorio_aberto(data_tentativa):
                    print(
                        f"Bartho: O consultório não abre na data escolhida "
                        f"para {nome_especialidade(consulta['especialidade'])}."
                    )
                elif data_tentativa.weekday() not in consulta["dias"]:
                    print(
                        f"Bartho: {consulta['medico']} "
                        f"({nome_especialidade(consulta['especialidade'])}) "
                        f"atende às {dias_texto}s."
                    )

        if not houve_progresso and not houve_escolha_invalida:
            print(
                "\nBartho: Não consegui associar os dias às consultas. "
                "Informe, por exemplo: "
                "\"Dermatologia terça e Clínica Geral quarta\"."
            )

    # --------------------------------------------------
    # DISPONIBILIDADE
    # --------------------------------------------------
    print(
        "\nBartho: Certo. Estes são os horários disponíveis "
        "para as datas escolhidas:"
    )

    for consulta in consultas:
        consulta["horarios_livres"] = consultar_horarios_livres(
            consulta["medico"],
            consulta["data_banco"]
        )

        print(
            f"\n- {nome_especialidade(consulta['especialidade'])} — "
            f"{consulta['medico']} — {consulta['data_banco']}"
        )

        if consulta["horarios_livres"]:
            print("  " + ", ".join(consulta["horarios_livres"]))
        else:
            print("  Sem horários disponíveis.")

    # Se uma data não tiver horários, troca somente aquela consulta.
    for consulta in consultas:
        while not consulta["horarios_livres"]:
            dias_texto = formatar_lista_dias(
                nomes_dias(consulta["dias"])
            )

            resposta = perguntar_preservando_fluxo(
                (
                    f"Não há horários livres para "
                    f"{nome_especialidade(consulta['especialidade'])}. "
                    f"{consulta['medico']} atende às {dias_texto}s. "
                    "Qual outro dia você prefere?"
                ),
                assunto_pendente="multiplos_data",
                especialidade=consulta["especialidade"]
            )

            # Aqui aceitamos resposta curta porque há só uma consulta pendente.
            interpretacao = interpretar_e_corrigir(resposta)
            data_texto = interpretacao.get("data", "nao_informado")
            consulta["data_texto"] = data_texto
            consulta["data"] = None

            if not validar_data_consulta(consulta):
                print("Bartho: Essa data não é válida para esse médico.")
                continue

            consulta["horarios_livres"] = consultar_horarios_livres(
                consulta["medico"],
                consulta["data_banco"]
            )

            if consulta["horarios_livres"]:
                print(
                    f"Bartho: {consulta['medico']} "
                    f"({nome_especialidade(consulta['especialidade'])}) "
                    f"tem estes horários em {consulta['data_banco']}:"
                )
                print(", ".join(consulta["horarios_livres"]))

    # Valida horários que talvez já tenham vindo na mensagem inicial.
    for consulta in consultas:
        horario = consulta.get("horario")

        if horario and horario not in [
            "nao_informado",
            "manha",
            "tarde"
        ]:
            horario = normalizar_horario(horario)
            if horario in consulta["horarios_livres"]:
                consulta["horario"] = horario
            else:
                consulta["horario"] = None
        else:
            consulta["horario"] = None

    # --------------------------------------------------
    # HORÁRIOS AGRUPADOS
    # --------------------------------------------------
    while any(c["horario"] is None for c in consultas):
        faltantes = [c for c in consultas if c["horario"] is None]

        print("\nBartho: Quais horários você prefere para cada consulta?")
        for c in faltantes:
            print(
                f"- {nome_especialidade(c['especialidade'])} — "
                f"{c['medico']} — {c['data_banco']}"
            )

        resposta = perguntar_preservando_fluxo(
            "Informe os horários para cada consulta.",
            assunto_pendente="multiplos_horarios",
            consultas_multiplas=consultas
        )
        interpretacao = interpretar_e_corrigir(resposta)
        aplicar_dados_interpretados(interpretacao)

        houve_progresso = False

        for consulta in faltantes:
            horario = consulta.get("horario")

            if not horario or horario in [
                "nao_informado",
                "manha",
                "tarde"
            ]:
                consulta["horario"] = None
                continue

            horario = normalizar_horario(horario)

            if horario in consulta["horarios_livres"]:
                consulta["horario"] = horario
                houve_progresso = True
            else:
                print(
                    f"Bartho: {horario} não está disponível para "
                    f"{nome_especialidade(consulta['especialidade'])}."
                )
                consulta["horario"] = None

        if not houve_progresso:
            print(
                "Bartho: Informe, por exemplo: "
                "\"Dermatologia 10:00 e Clínica Geral 14:00\"."
            )

    # --------------------------------------------------
    # PACIENTE — UMA VEZ
    # --------------------------------------------------
    paciente = ""
    while not paciente:
        paciente = perguntar_preservando_fluxo(
            "Perfeito. Qual é o nome do paciente para esses agendamentos?",
            assunto_pendente="multiplos_paciente"
        )

        if not paciente:
            print(
                "Bartho: Preciso do nome do paciente para continuar."
            )

    # --------------------------------------------------
    # RESUMO E CONFIRMAÇÃO ÚNICA
    # --------------------------------------------------
    print("\n=== RESUMO DOS AGENDAMENTOS ===")
    print("Paciente:", paciente)

    for indice, consulta in enumerate(consultas, start=1):
        print(
            f"{indice}. "
            f"{nome_especialidade(consulta['especialidade'])} — "
            f"{consulta['medico']} — "
            f"{consulta['data_banco']} às {consulta['horario']}"
        )

    confirmacao = perguntar_preservando_fluxo(
        f"Deseja confirmar os {len(consultas)} agendamentos?",
        assunto_pendente="multiplos_confirmacao"
    ).lower().strip()

    if confirmacao not in [
        "sim",
        "s",
        "yes",
        "confirmo",
        "pode",
        "pode sim"
    ]:
        print("\nBartho: Os agendamentos não foram confirmados.")
        return paciente

    # Revalida o conjunto antes de gravar.
    for consulta in consultas:
        horarios_atuais = consultar_horarios_livres(
            consulta["medico"],
            consulta["data_banco"]
        )

        if consulta["horario"] not in horarios_atuais:
            print(
                f"\nBartho: O horário {consulta['horario']} de "
                f"{nome_especialidade(consulta['especialidade'])} "
                "não está mais disponível."
            )
            print(
                "Bartho: Nenhum dos agendamentos foi criado."
            )
            return paciente

    agendamentos_criados = []

    for consulta in consultas:
        sucesso = criar_agendamento(
            paciente,
            consulta["medico"],
            consulta["data_banco"],
            consulta["horario"]
        )

        if sucesso:
            agendamentos_criados.append(consulta)
            continue

        # Compensação para evitar conjunto parcialmente criado.
        for criada in agendamentos_criados:
            for agendamento in buscar_agendamentos(paciente):
                if (
                    agendamento[1] == criada["medico"]
                    and agendamento[3] == criada["data_banco"]
                    and agendamento[4] == criada["horario"]
                ):
                    cancelar_agendamento(agendamento[0])
                    break

        print(
            "\nBartho: Não foi possível concluir o conjunto de "
            "agendamentos. As consultas desta operação não foram mantidas."
        )
        return paciente

    print(
        f"\nBartho: Os {len(consultas)} agendamentos "
        "foram realizados com sucesso!"
    )

    for consulta in consultas:
        print(
            f"- {nome_especialidade(consulta['especialidade'])} — "
            f"{consulta['medico']} — "
            f"{consulta['data_banco']} às {consulta['horario']}"
        )

    # Preserva TODAS as consultas confirmadas para perguntas posteriores.
    consultas_contexto = []
    agendamentos_paciente = buscar_agendamentos(paciente)
    for consulta in consultas:
        agendamento_id = None
        for ag in agendamentos_paciente:
            if (
                ag[1] == consulta["medico"]
                and ag[3] == consulta["data_banco"]
                and ag[4] == consulta["horario"]
            ):
                agendamento_id = ag[0]
                break
        consultas_contexto.append({
            "paciente": paciente,
            "especialidade": consulta["especialidade"],
            "medico": consulta["medico"],
            "data": consulta["data_banco"],
            "horario": consulta["horario"],
            "agendamento_id": agendamento_id
        })
    registrar_consultas_sessao(consultas_contexto)
    registrar_resolucao("agendamento")
    marcar_operacao_concluida()
    return paciente

# ==================================================
# CANCELAMENTO
# ==================================================

def fluxo_cancelamento(mensagem_inicial="", interpretacao_inicial=None):
    paciente = perguntar_preservando_fluxo(
        "Qual é o nome do paciente?",
        assunto_pendente="cancelamento_paciente"
    )
    consultas = buscar_agendamentos(paciente)
    if not consultas:
        print("\nBartho: Não encontrei consultas para esse paciente.")
        return

    # A mensagem inicial pode já indicar especialidade/médico; se não, pergunta.
    escolha_inicial = mensagem_inicial if _indices_por_escolha_natural(mensagem_inicial, consultas) else None
    selecionadas, _ = selecionar_consultas(
        consultas, permitir_multiplas=True, escolha_inicial=escolha_inicial
    )
    if not selecionadas:
        return

    print("\n=== CONSULTA(S) PARA CANCELAMENTO ===")
    for consulta in selecionadas:
        print(f"- {consulta[1]} | {consulta[2]} | {consulta[3]} | {consulta[4]}")

    plural = len(selecionadas) > 1
    confirmacao = perguntar_preservando_fluxo(
        "Deseja realmente cancelar essas consultas?" if plural else "Deseja realmente cancelar essa consulta?",
        assunto_pendente="cancelamento_confirmacao"
    ).lower().strip()

    if confirmacao not in ["sim", "s", "yes", "confirmo", "pode", "pode sim"]:
        print("\nBartho: Cancelamento não confirmado.")
        return

    canceladas = 0
    for consulta in selecionadas:
        agendamento_id, medico, especialidade, data, horario = consulta[:5]
        if cancelar_agendamento(agendamento_id):
            canceladas += 1
            remover_consulta_da_sessao(
                agendamento_id, paciente, medico, data, horario
            )

    if canceladas == len(selecionadas):
        print(
            f"\nBartho: {canceladas} consultas canceladas com sucesso!"
            if plural else "\nBartho: Consulta cancelada com sucesso!"
        )
        marcar_operacao_concluida()
    else:
        print(f"\nBartho: Consegui cancelar {canceladas} de {len(selecionadas)} consulta(s).")



# ==================================================
# REMARCAÇÃO
# ==================================================

def _dados_remarcacao_da_mensagem(mensagem, interpretacao, consulta):
    """Aproveita seleção/data/horário já fornecidos, sem inventar dados."""
    texto = normalizar_mensagem_curta(mensagem or "")
    data_texto = None
    horario = None

    if "mesmo dia" in texto or "mesma data" in texto or "dia mesmo" in texto:
        data_texto = consulta[3]
    elif interpretacao:
        valor = interpretacao.get("data")
        if valor and valor != "nao_informado":
            data_texto = valor

    if interpretacao:
        valor = interpretacao.get("horario")
        if valor and valor not in ("nao_informado", "manha", "tarde"):
            horario = normalizar_horario(valor)

    # Fallback determinístico para "às 16", "16h", "16:00".
    if not horario:
        # Prioriza marcas inequívocas de horário para não confundir "dia 05" com 05:00.
        m = re.search(r"\bas\s+(\d{1,2})(?::(\d{2}))?\b", texto)
        if not m:
            m = re.search(r"\b(\d{1,2}):(\d{2})\b", texto)
        if not m:
            m = re.search(r"\b(\d{1,2})h(?:(\d{2}))?\b", texto)
        if m:
            h = int(m.group(1))
            minuto = int(m.group(2) or 0)
            if 0 <= h <= 23 and 0 <= minuto <= 59:
                horario = f"{h:02d}:{minuto:02d}"

    return data_texto, horario


def fluxo_remarcacao(mensagem_inicial="", interpretacao_inicial=None):
    paciente = perguntar_preservando_fluxo(
        "Qual é o nome do paciente?",
        assunto_pendente="remarcacao_paciente"
    )
    consultas = buscar_agendamentos(paciente)
    if not consultas:
        print("\nBartho: Não encontrei consultas para esse paciente.")
        return

    escolha_inicial = mensagem_inicial if _indices_por_escolha_natural(mensagem_inicial, consultas) else None
    selecionadas, mensagem_escolha = selecionar_consultas(
        consultas, permitir_multiplas=False, escolha_inicial=escolha_inicial
    )
    if not selecionadas:
        return
    consulta = selecionadas[0]
    agendamento_id, medico, especialidade, data_atual, horario_atual = consulta[:5]

    print("\n=== CONSULTA ATUAL ===")
    print("Paciente:", paciente)
    print("Médico:", medico)
    print("Especialidade:", especialidade)
    print("Data:", data_atual)
    print("Horário:", horario_atual)

    # Junta os dados já fornecidos antes e durante a escolha.
    interpretacao_escolha = None
    texto_dados = " ".join(x for x in (mensagem_inicial, mensagem_escolha) if x).strip()
    if texto_dados and texto_dados != mensagem_inicial:
        # Só chama o interpretador se houver sinal de data/horário além da seleção.
        norm = normalizar_mensagem_curta(texto_dados)
        if any(t in norm for t in ("dia", "amanha", "segunda", "terca", "quarta", "quinta", "sexta", "hora", ":", " as ")):
            interpretacao_escolha = interpretar_e_corrigir(texto_dados)
    dados_base = interpretacao_escolha or interpretacao_inicial or {}
    nova_data_texto, novo_horario = _dados_remarcacao_da_mensagem(texto_dados, dados_base, consulta)

    dias_medico = consultar_dias_medico(medico)
    texto_dias = formatar_lista_dias(nomes_dias(dias_medico))
    nova_data = None

    while nova_data is None:
        if not nova_data_texto:
            nova_data_texto = perguntar_preservando_fluxo(
                "Para qual dia você deseja remarcar?",
                assunto_pendente="remarcacao_data",
                especialidade=especialidade
            )
        data_tentativa = resolver_data(nova_data_texto)
        nova_data_texto = None
        if data_tentativa is None:
            print("\nBartho: Não consegui interpretar essa data.")
            continue
        if not consultorio_aberto(data_tentativa):
            print("\nBartho: O consultório não abre nessa data.")
            continue
        if data_tentativa.weekday() not in dias_medico:
            print(f"\nBartho: {medico} não está disponível nessa data.")
            print(f"Bartho: Ele atende às {texto_dias}s.")
            continue
        nova_data = data_tentativa

    nova_data_banco = nova_data.strftime("%d/%m/%Y")
    horarios = consultar_horarios_livres(medico, nova_data_banco)
    if nova_data_banco == data_atual and horario_atual not in horarios:
        horarios.append(horario_atual)
        horarios.sort()
    if not horarios:
        print("\nBartho: Não existem horários disponíveis nessa data.")
        return

    print("\nBartho: Horários disponíveis:", ", ".join(horarios))
    if novo_horario:
        novo_horario = normalizar_horario(novo_horario)
        if novo_horario not in horarios:
            print(f"\nBartho: {novo_horario} não está disponível nessa data.")
            novo_horario = None

    while novo_horario is None:
        escolha = perguntar_preservando_fluxo(
            "Qual novo horário você prefere?",
            assunto_pendente="remarcacao_horario",
            especialidade=especialidade
        )
        horario_tentativa = normalizar_horario(escolha)
        if horario_tentativa not in horarios:
            print("\nBartho: Esse horário não está disponível.")
            continue
        novo_horario = horario_tentativa

    print(
        f"\nBartho: Deseja remarcar a consulta de {data_atual} às {horario_atual} "
        f"para {nova_data_banco} às {novo_horario}?"
    )
    confirmacao = perguntar_preservando_fluxo(
        "Confirma essa remarcação?",
        assunto_pendente="remarcacao_confirmacao",
        especialidade=especialidade
    ).lower().strip()

    if confirmacao in ["sim", "s", "yes", "confirmo", "pode", "pode sim"]:
        sucesso = remarcar_agendamento(agendamento_id, nova_data_banco, novo_horario)
        if sucesso:
            print("\nBartho: Consulta remarcada com sucesso!")
            print(f"{paciente} | {medico} | {nova_data_banco} | {novo_horario}")
            atualizar_consulta_da_sessao(
                agendamento_id, paciente, especialidade, medico,
                nova_data_banco, novo_horario
            )
            marcar_operacao_concluida()
        else:
            print("\nBartho: Não foi possível remarcar a consulta.")
    else:
        print("\nBartho: Remarcação não confirmada.")



# ==================================================
# CONSULTA DE DISPONIBILIDADE CONVERSACIONAL
# ==================================================

def fluxo_consulta(
    interpretacao,
    mensagem
):
    estado = EstadoConversa()
    estado.atualizar(
        interpretacao
    )

    # ----------------------------------------------
    # ESPECIALIDADE
    # ----------------------------------------------

    while not estado.especialidade:

        resposta = perguntar_preservando_fluxo(
            "Qual especialidade você procura?",
            assunto_pendente="consulta_especialidade"
        )

        nova_interpretacao = (
            interpretar_e_corrigir(
                resposta
            )
        )

        nova_interpretacao[
            "intencao"
        ] = "consultar"

        estado.atualizar(
            nova_interpretacao
        )

    # ----------------------------------------------
    # DATA
    # ----------------------------------------------

    data_resolvida = None
    medico = None

    while data_resolvida is None:

        if not estado.data:

            resposta = perguntar_preservando_fluxo(
                "Para qual dia você gostaria de consultar os horários?",
                assunto_pendente="consulta_data",
                especialidade=estado.especialidade
            )

            nova_interpretacao = (
                interpretar_e_corrigir(
                    resposta
                )
            )

            nova_interpretacao[
                "intencao"
            ] = "consultar"

            estado.atualizar(
                nova_interpretacao
            )

        data_tentativa = resolver_data(
            estado.data
        )

        if data_tentativa is None:
            print(
                "\nBartho: Não consegui "
                "interpretar essa data."
            )
            estado.data = None
            continue

        if not consultorio_aberto(
            data_tentativa
        ):
            print(
                "\nBartho: O consultório "
                "não abre nessa data."
            )
            estado.data = None
            continue

        dia_semana = (
            data_tentativa.weekday()
        )

        medicos = verificar_atendimento(
            estado.especialidade,
            dia_semana
        )

        if not medicos:

            medicos_especialidade = (
                consultar_medicos_por_especialidade(
                    estado.especialidade
                )
            )

            print(
                f"\nBartho: Não há atendimento de "
                f"{nome_especialidade(estado.especialidade)} "
                f"nesse dia."
            )

            if medicos_especialidade:
                medico_temp = (
                    medicos_especialidade[0]
                )

                dias = consultar_dias_medico(
                    medico_temp
                )

                texto_dias = (
                    formatar_lista_dias(
                        nomes_dias(dias)
                    )
                )

                print(
                    f"Bartho: {medico_temp} atende "
                    f"às {texto_dias}s."
                )

            estado.data = None
            continue

        medico = medicos[0][0]
        data_resolvida = data_tentativa

    data_banco = (
        data_resolvida.strftime(
            "%d/%m/%Y"
        )
    )

    horarios_livres = (
        consultar_horarios_livres(
            medico,
            data_banco
        )
    )

    if not horarios_livres:
        print(
            "\nBartho: Não existem horários "
            "disponíveis nessa data."
        )
        return

    # ----------------------------------------------
    # PREFERÊNCIA
    # ----------------------------------------------

    if (
        not estado.horario
        or estado.horario == "nao_informado"
    ):

        resposta = perguntar_preservando_fluxo(
            "Você prefere manhã, tarde ou qualquer horário?",
            assunto_pendente="consulta_periodo",
            especialidade=estado.especialidade
        )

        resposta_normalizada = (
            resposta.lower()
        )

        if resposta_normalizada in [
            "qualquer",
            "qualquer horario",
            "qualquer horário",
            "tanto faz",
            "todos",
            "todos os horarios",
            "todos os horários"
        ]:
            estado.horario = "qualquer"

        else:
            nova_interpretacao = (
                interpretar_e_corrigir(
                    resposta
                )
            )

            horario_interpretado = (
                nova_interpretacao[
                    "horario"
                ]
            )

            if (
                horario_interpretado
                != "nao_informado"
            ):
                estado.horario = (
                    horario_interpretado
                )
            else:
                estado.horario = (
                    "qualquer"
                )

    # ----------------------------------------------
    # FILTRAR
    # ----------------------------------------------

    horarios_resultado = (
        horarios_livres
    )

    if estado.horario == "manha":
        horarios_resultado = [
            h
            for h in horarios_livres
            if 8 <= int(h[:2]) < 12
        ]

    elif estado.horario == "tarde":
        horarios_resultado = [
            h
            for h in horarios_livres
            if 12 <= int(h[:2]) <= 17
        ]

    elif estado.horario not in [
        None,
        "qualquer",
        "nao_informado"
    ]:
        horario_exato = (
            normalizar_horario(
                estado.horario
            )
        )

        if horario_exato in horarios_livres:
            horarios_resultado = [
                horario_exato
            ]
        else:
            horarios_resultado = []

    print("\n=== BARTHO ===")
    print(
        "Especialidade:",
        nome_especialidade(
            estado.especialidade
        )
    )
    print("Médico:", medico)
    print("Data:", data_banco)

    if not horarios_resultado:
        print(
            "Bartho: Não há horários disponíveis "
            "para essa preferência."
        )
        return

    print(
        "Horários disponíveis:",
        ", ".join(horarios_resultado)
    )

    # ----------------------------------------------
    # CONTINUAR A CONVERSA
    # ----------------------------------------------

    horario_exato_informado = (
        estado.horario not in [
            None,
            "qualquer",
            "nao_informado",
            "manha",
            "tarde"
        ]
        and len(horarios_resultado) == 1
    )

    if horario_exato_informado:
        horario_exato = horarios_resultado[0]

        resposta = perguntar_preservando_fluxo(
            f"O horário {horario_exato} está disponível. Deseja agendar?",
            assunto_pendente="consulta_deseja_agendar",
            especialidade=estado.especialidade
        ).lower().strip()

        if resposta in [
            "sim",
            "s",
            "quero",
            "claro",
            "pode",
            "pode sim",
            "pode ser"
        ]:
            finalizar_agendamento(
                estado.especialidade,
                medico,
                data_banco,
                horario_exato
            )
        else:
            print(
                "\nBartho: Tudo bem! "
                "Quando precisar, estou por aqui."
            )

        return

    while True:

        resposta = perguntar_preservando_fluxo(
            (
                "Se quiser agendar, escolha um desses horários. "
                "Se queria apenas consultar, é só me avisar."
            ),
            assunto_pendente="consulta_horario",
            especialidade=estado.especialidade
        )

        if resposta.lower() in [
            "sair",
            "não",
            "nao",
            "não obrigado",
            "nao obrigado",
            "só consultar",
            "so consultar"
        ]:
            print(
                "\nBartho: Tudo bem! "
                "Quando precisar, estou por aqui."
            )
            return

        horario_escolhido = (
            normalizar_horario(
                resposta
            )
        )

        if (
            horario_escolhido
            in horarios_resultado
        ):
            finalizar_agendamento(
                estado.especialidade,
                medico,
                data_banco,
                horario_escolhido
            )
            return

        print(
            "\nBartho: Escolha um dos horários "
            "disponíveis ou me diga que queria apenas consultar."
        )


# ==================================================
# LISTAR ESPECIALIDADES
# ==================================================

def fluxo_especialidades():

    especialidades = (
        consultar_especialidades()
    )

    if not especialidades:
        print(
            "\nBartho: Não encontrei "
            "especialidades cadastradas."
        )
        return

    especialidades_formatadas = [
        nome_especialidade(e)
        for e in especialidades
    ]

    print(
        "\nBartho: Atualmente temos "
        + ", ".join(
            especialidades_formatadas
        )
        + "."
    )

    resposta = perguntar_preservando_fluxo(
        "Qual delas você procura?",
        assunto_pendente="especialidades_especialidade"
    )

    interpretacao = (
        interpretar_e_corrigir(
            resposta
        )
    )

    especialidade = (
        interpretacao[
            "especialidade"
        ]
    )

    if especialidade == "nao_informado":
        print(
            "\nBartho: Não consegui identificar "
            "a especialidade."
        )
        return

    medicos = (
        consultar_medicos_por_especialidade(
            especialidade
        )
    )

    if not medicos:
        print(
            "\nBartho: Essa especialidade não "
            "está cadastrada na clínica."
        )
        return

    medico = medicos[0]

    dias = consultar_dias_medico(
        medico
    )

    texto_dias = formatar_lista_dias(
        nomes_dias(dias)
    )

    print(
        f"\nBartho: {medico} atende "
        f"{nome_especialidade(especialidade)} "
        f"às {texto_dias}s."
    )

    resposta = perguntar_preservando_fluxo(
        "Quer consultar os horários disponíveis?",
        assunto_pendente="especialidades_deseja_agendar",
        especialidade=especialidade
    ).lower().strip()

    if resposta in [
        "sim",
        "s",
        "quero",
        "pode ser",
        "claro"
    ]:

        interpretacao_consulta = {
            "intencao": "consultar",
            "especialidade": especialidade,
            "especialidades": [especialidade],
            "data": "nao_informado",
            "horario": "nao_informado"
        }

        fluxo_consulta(
            interpretacao_consulta,
            ""
        )

    else:
        print(
            "\nBartho: Tudo bem! "
            "Quando precisar, estou por aqui."
        )


# ==================================================
# CONSULTAR MÉDICO / ESPECIALIDADE
# ==================================================

def fluxo_consultar_medico(
    interpretacao
):

    especialidade = (
        interpretacao[
            "especialidade"
        ]
    )

    if especialidade == "nao_informado":

        resposta = perguntar_preservando_fluxo(
            "Qual especialidade você gostaria de consultar?",
            assunto_pendente="medico_especialidade"
        )

        nova_interpretacao = (
            interpretar_e_corrigir(
                resposta
            )
        )

        especialidade = (
            nova_interpretacao[
                "especialidade"
            ]
        )

    if especialidade == "nao_informado":
        print(
            "\nBartho: Não consegui identificar "
            "a especialidade."
        )
        return

    medicos = (
        consultar_medicos_por_especialidade(
            especialidade
        )
    )

    if not medicos:
        print(
            f"\nBartho: No momento não encontrei "
            f"{nome_especialidade(especialidade)} "
            f"cadastrada na clínica."
        )
        return

    for medico in medicos:

        dias = consultar_dias_medico(
            medico
        )

        texto_dias = (
            formatar_lista_dias(
                nomes_dias(dias)
            )
        )

        print(
            f"\nBartho: {medico} atende "
            f"{nome_especialidade(especialidade)} "
            f"às {texto_dias}s."
        )

    resposta = perguntar_preservando_fluxo(
        "Quer consultar os horários disponíveis?",
        assunto_pendente="medico_deseja_agendar",
        especialidade=especialidade
    ).lower().strip()

    if resposta in [
        "sim",
        "s",
        "quero",
        "pode ser",
        "claro"
    ]:

        interpretacao_consulta = {
            "intencao": "consultar",
            "especialidade": especialidade,
            "especialidades": [especialidade],
            "data": "nao_informado",
            "horario": "nao_informado"
        }

        fluxo_consulta(
            interpretacao_consulta,
            ""
        )

    else:
        print(
            "\nBartho: Tudo bem! "
            "Quando precisar, estou por aqui."
        )


# ==================================================
# SAUDAÇÃO
# ==================================================

def exibir_saudacao():
    print(
        "\nBartho: Olá! Sou o Bartho, "
        "assistente virtual da Clínica Bem."
    )


# ==================================================
# HANDOFF PARA ATENDIMENTO HUMANO
# ==================================================

def oferecer_atendimento_humano(mensagem):

    print(
        "\nBartho: Esse assunto precisa de ajuda "
        "da equipe da clínica."
    )

    resposta = ler_entrada_usuario(
        "Bartho: Posso encaminhar sua solicitação "
        "para um atendente humano?\n"
        "Você: "
    ).lower().strip()

    if resposta in [
        "sim",
        "s",
        "quero",
        "claro",
        "pode",
        "pode sim",
        "pode ser"
    ]:

        sucesso = registrar_atendimento_humano(
            mensagem,
            "nao_compreendido_apos_esclarecimento"
        )

        if sucesso:
            print(
                "\nBartho: Certo. Sua solicitação foi "
                "registrada para atendimento humano."
            )
            print(
                "Bartho: Status: aguardando atendente."
            )
        else:
            print(
                "\nBartho: Não consegui registrar o "
                "encaminhamento neste momento."
            )

        return

    print(
        "\nBartho: Sem problema. "
        "O encaminhamento não foi realizado."
    )


# ==================================================
# RAG — BASE DE CONHECIMENTO DA CLÍNICA BEM
# ==================================================

def _chamar_qwen_texto_sem_metricas(prompt):
    """Chamada local ao Qwen para geração curta/controlada."""
    try:
        dados = {
            "model": "qwen3:1.7b",
            "prompt": prompt,
            "stream": False
        }

        requisicao = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps(dados).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(requisicao, timeout=120) as resposta:
            resultado = json.loads(resposta.read().decode("utf-8"))

        return resultado.get("response", "").strip()

    except Exception:
        return ""


def chamar_qwen_texto(*args, **kwargs):
    inicio = time.perf_counter()
    try:
        return _chamar_qwen_texto_sem_metricas(*args, **kwargs)
    except Exception:
        registrar_erro()
        raise
    finally:
        registrar_qwen(time.perf_counter() - inicio)


def registrar_contexto(assunto, pergunta, resposta, especialidade=None, pendencia=None, plano=None):
    contexto_conversa["assunto_atual"] = assunto
    contexto_conversa["pergunta_anterior"] = pergunta
    contexto_conversa["resposta_anterior"] = resposta
    if especialidade is not None:
        contexto_conversa["especialidade_atual"] = especialidade
    if plano is not None:
        contexto_conversa["plano_atual"] = plano
    contexto_conversa["pendencia"] = pendencia


def ler_secoes_base(nomes_secoes):
    """
    Lê somente as seções pedidas da base de conhecimento.
    Isso evita entregar ao LLM blocos irrelevantes, como dias/médicos
    quando a pergunta é apenas sobre documentos.
    """
    try:
        with open("base_conhecimento.txt", "r", encoding="utf-8") as arquivo:
            linhas = arquivo.readlines()
    except Exception:
        return ""

    nomes = {nome.upper() for nome in nomes_secoes}
    atual = None
    selecionadas = []

    for linha in linhas:
        limpa = linha.strip()

        if limpa.startswith("[") and limpa.endswith("]"):
            atual = limpa[1:-1].strip().upper()
            continue

        if atual in nomes and limpa:
            selecionadas.append(limpa)

    return "\n".join(selecionadas)


def extrair_especialidade_texto(mensagem, interpretacao=None):
    if interpretacao:
        especialidade = interpretacao.get("especialidade")
        if especialidade in {"cardiologia", "dermatologia", "clinica_geral"}:
            return especialidade

    texto = normalizar_mensagem_curta(mensagem)

    if "cardiologia" in texto or "cardiologista" in texto:
        return "cardiologia"

    if "dermatologia" in texto or "dermatologista" in texto:
        return "dermatologia"

    if "clinica geral" in texto or "clinico geral" in texto:
        return "clinica_geral"

    return None


def pergunta_sobre_documentos_ou_preparo(mensagem):
    texto = normalizar_mensagem_curta(mensagem)

    termos = (
        "o que preciso levar",
        "o que devo levar",
        "preciso levar",
        "devo levar",
        "quais documentos",
        "qual documento",
        "documentacao",
        "preparacao",
        "como me preparar",
        "precisa levar exame",
        "preciso levar exame"
    )

    return any(termo in texto for termo in termos)


def responder_documentos_consulta(mensagem, interpretacao):
    """Responde documentos/preparo da base e preserva contexto de uma ou várias consultas."""
    if not pergunta_sobre_documentos_ou_preparo(mensagem):
        return False

    especialidade = extrair_especialidade_texto(mensagem, interpretacao)
    consultas_sessao = contexto_conversa.get("consultas_sessao", [])
    especialidades_contexto = []
    for consulta in consultas_sessao:
        esp = consulta.get("especialidade")
        if esp and esp not in especialidades_contexto:
            especialidades_contexto.append(esp)

    if especialidade:
        especialidades_contexto = [especialidade]
    elif not especialidades_contexto:
        esp = especialidade_da_sessao()
        if esp:
            especialidades_contexto = [esp]

    contexto = ler_secoes_base(["DOCUMENTOS", "PREPARAÇÃO PARA CONSULTA"])
    if not contexto:
        return False

    texto = normalizar_mensagem_curta(mensagem)
    pergunta_preparo = any(t in texto for t in (
        "preparacao", "como me preparar", "precisa levar exame", "preciso levar exame"
    ))

    partes = []
    if len(especialidades_contexto) > 1:
        nomes = [nome_especialidade(e) for e in especialidades_contexto]
        if len(nomes) == 2:
            lista = " e ".join(nomes)
        else:
            lista = ", ".join(nomes[:-1]) + " e " + nomes[-1]
        partes.append(f"Para suas consultas de {lista}, leve um documento oficial com foto.")
    else:
        partes.append("Para a consulta, leve um documento oficial com foto.")

    partes.append("Se utilizar convênio, leve também a carteirinha do convênio, quando aplicável.")
    partes.append("Menores de idade devem estar acompanhados por responsável.")

    if pergunta_preparo:
        if especialidades_contexto:
            nomes = ", ".join(nome_especialidade(e) for e in especialidades_contexto)
            partes.append(
                f"Não há outra orientação específica de preparo para {nomes} registrada na base da Clínica Bem."
            )
        else:
            partes.append("Não há outra orientação específica de preparo registrada na base da Clínica Bem.")

    resposta = " ".join(partes)
    print(f"\nBartho: {resposta}")
    registrar_contexto(
        "documentos_consulta", mensagem, resposta,
        especialidade=(especialidades_contexto[-1] if especialidades_contexto else None)
    )
    registrar_rag()
    registrar_resolucao("informacao_rag")
    return True


def tentar_resolver_pendencia(mensagem, interpretacao):
    if contexto_conversa["pendencia"] != "especialidade_documentos":
        return False

    especialidade = extrair_especialidade_texto(mensagem, interpretacao)

    if not especialidade:
        return False

    contexto = ler_secoes_base(["PREPARAÇÃO PARA CONSULTA"])

    if not contexto:
        return False

    nome = nome_especialidade(especialidade)

    prompt = f"""
Você é o Bartho, assistente virtual da Clínica Bem.

O usuário já recebeu os documentos gerais e agora informou a especialidade: {nome}.

Use SOMENTE a base abaixo.

Regras:
- Informe SOMENTE a orientação específica de {nome}.
- Não repita os documentos gerais.
- Ignore completamente as outras especialidades.
- Não acrescente médico, dias de atendimento, horários ou qualquer assunto não perguntado.
- Não invente informações.
- Seja breve.

BASE AUTORIZADA:
{contexto}

RESPOSTA:
""".strip()

    resposta = chamar_qwen_texto(prompt)

    if not resposta:
        return False

    print(f"\nBartho: {resposta}")
    registrar_contexto(
        "documentos_consulta",
        mensagem,
        resposta,
        especialidade=especialidade
    )
    return True


def responder_continuacao_contextual(mensagem):
    """
    Para mensagens curtas e dependentes do turno anterior, o próprio Qwen
    decide se a frase é uma continuação. A resposta continua limitada à
    seção correta da base, em vez de usar listas de frases hardcoded.
    """
    assunto = contexto_conversa.get("assunto_atual")
    pergunta_anterior = contexto_conversa.get("pergunta_anterior")
    resposta_anterior = contexto_conversa.get("resposta_anterior")

    if not assunto or not resposta_anterior:
        return False

    texto = normalizar_mensagem_curta(mensagem)

    # Pedidos novos/longos seguem o roteamento normal.
    if len(texto.split()) > 8:
        return False

    if assunto == "documentos_consulta":
        contexto = ler_secoes_base(["DOCUMENTOS", "PREPARAÇÃO PARA CONSULTA"])
    else:
        return False

    prompt = f"""
Classifique a mensagem atual em apenas uma destas opções:
CONTINUACAO
NOVO_ASSUNTO

CONTINUACAO = a mensagem depende claramente da resposta anterior, por exemplo
uma confirmação, dúvida curta, pedido de complemento ou referência como
"isso", "mais alguma coisa", "só isso", "e se for...".
NOVO_ASSUNTO = inicia outro pedido independente.

Pergunta anterior: {pergunta_anterior}
Resposta anterior: {resposta_anterior}
Mensagem atual: {mensagem}

Responda SOMENTE CONTINUACAO ou NOVO_ASSUNTO.
""".strip()

    classificacao = chamar_qwen_texto(prompt).upper()

    if "CONTINUACAO" not in classificacao:
        return False

    especialidade = contexto_conversa.get("especialidade_atual")
    especialidade_texto = (
        nome_especialidade(especialidade)
        if especialidade
        else "não informada"
    )

    prompt_resposta = f"""
Você é o Bartho, assistente virtual da Clínica Bem.

Responda à mensagem atual levando em conta o turno anterior.
Use SOMENTE a base autorizada.

Regras:
- Responda exatamente à dúvida atual.
- Não acrescente informações que não foram perguntadas.
- Não invente informações.
- Se a pergunta for se existe algo além do que já foi informado, confirme
  somente o que a base realmente prevê.
- Não cite médico, dias, horários ou outros assuntos fora da pergunta.
- Seja breve e natural.

Especialidade atual: {especialidade_texto}

BASE AUTORIZADA:
{contexto}

Pergunta anterior: {pergunta_anterior}
Resposta anterior: {resposta_anterior}
Mensagem atual: {mensagem}

RESPOSTA:
""".strip()

    resposta = chamar_qwen_texto(prompt_resposta)

    if not resposta:
        return False

    print(f"\nBartho: {resposta}")
    registrar_contexto(
        assunto,
        mensagem,
        resposta,
        especialidade=especialidade
    )
    return True


def _responder_rag_verificado_sem_metricas(pergunta, especialidade=None, secoes=None):
    """
    Resposta informativa com abstention:
    1) restringe a fonte quando possível;
    2) exige que o modelo aponte evidência textual da própria base;
    3) se a base não sustenta a resposta, não completa lacunas.
    """
    if secoes:
        contexto = ler_secoes_base(secoes)
        contextos = [contexto] if contexto else []
    else:
        contextos = buscar_contexto(pergunta, limite=2)

    if not contextos:
        return False

    contexto = "\n\n".join(contextos)
    especialidade_texto = (
        nome_especialidade(especialidade)
        if especialidade
        else "não informada"
    )

    prompt = f"""
Você é o Bartho, assistente virtual da Clínica Bem.
Sua resposta só pode afirmar fatos explicitamente sustentados pela BASE.

BASE:
{contexto}

PERGUNTA:
{pergunta}

Especialidade atual: {especialidade_texto}

Retorne SOMENTE JSON válido:
{{
  "suportado": true ou false,
  "evidencia": "trecho curto da BASE que sustenta a resposta, ou vazio",
  "resposta": "resposta breve ao paciente"
}}

Regras:
- Se a BASE não responder exatamente ao que foi perguntado, suportado=false.
- Não use conhecimento externo, suposições, práticas comuns ou inferências sobre planos de saúde.
- O fato de a clínica aceitar um convênio NÃO significa que o convênio cobre todos os serviços.
- Não transforme "valores particulares devem ser confirmados" em informação sobre cobertura do convênio.
- Responda somente ao que foi perguntado.
- Não acrescente fatos de outros assuntos.
""".strip()

    bruto = chamar_qwen_texto(prompt)
    if not bruto:
        return False

    try:
        inicio = bruto.index("{")
        fim = bruto.rindex("}") + 1
        dados = json.loads(bruto[inicio:fim])
    except Exception:
        return False

    if not dados.get("suportado"):
        return False

    evidencia = str(dados.get("evidencia", "")).strip()
    resposta = str(dados.get("resposta", "")).strip()

    if not evidencia or not resposta:
        return False

    # Verificação simples adicional: a evidência declarada precisa estar
    # literalmente na base fornecida (ignorando caixa/espaços).
    def compactar(s):
        return " ".join(s.lower().split())

    if compactar(evidencia) not in compactar(contexto):
        return False

    print(f"\nBartho: {resposta}")
    registrar_contexto(
        "informacao_clinica",
        pergunta,
        resposta,
        especialidade=especialidade
    )
    return True


def responder_rag_verificado(*args, **kwargs):
    resultado = _responder_rag_verificado_sem_metricas(*args, **kwargs)
    if resultado:
        registrar_rag()
        registrar_resolucao("informacao_rag")
    return resultado


def responder_sem_inventar(pergunta):
    """Explica a limitação sem transformar falta de informação em handoff automático."""
    print(
        "\nBartho: Não tenho essa informação confirmada na base da Clínica Bem. "
        "Posso ajudar com informações e serviços que a clínica oferece, como "
        "consultas, especialidades, horários, convênios aceitos e agendamentos."
    )
    registrar_contexto(
        "informacao_nao_disponivel",
        pergunta,
        "Informação não confirmada; limite explicado sem encaminhamento automático."
    )
    return True


def _responder_com_rag_sem_metricas(pergunta, especialidade=None):
    return _responder_rag_verificado_sem_metricas(pergunta, especialidade=especialidade)


def responder_com_rag(*args, **kwargs):
    resultado = _responder_com_rag_sem_metricas(*args, **kwargs)
    if resultado:
        registrar_rag()
        registrar_resolucao("informacao_rag")
    return resultado


def normalizar_mensagem_curta(mensagem):
    import unicodedata
    import re

    texto = mensagem.lower().strip()
    texto = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    texto = re.sub(r"[^\w\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def tem_contexto_conversacional_relevante():
    """Indica se o turno atual realmente depende de contexto anterior."""
    return bool(
        contexto_conversa.get("pendencia")
        or contexto_conversa.get("aguardando_mais_alguma_coisa")
        or contexto_conversa.get("ultima_consulta")
        or contexto_conversa.get("assunto_atual")
        or contexto_conversa.get("pergunta_anterior")
        or contexto_conversa.get("resposta_anterior")
    )


def interpretar_turno_contextual(mensagem):
    """
    Interpreta a mensagem junto com o estado e os últimos turnos.
    A conversa fica aberta; as operações continuam limitadas às tools/fluxos seguros.
    """
    consulta = contexto_conversa.get("ultima_consulta")
    estado_resumido = {
        "assunto_atual": contexto_conversa.get("assunto_atual"),
        "especialidade_atual": contexto_conversa.get("especialidade_atual"),
        "plano_atual": contexto_conversa.get("plano_atual"),
        "pendencia": contexto_conversa.get("pendencia"),
        "aguardando_mais_alguma_coisa": contexto_conversa.get("aguardando_mais_alguma_coisa"),
        "ultima_consulta": consulta
    }

    prompt = f"""
Você é a camada de interpretação contextual do Bartho, assistente da Clínica Bem.

Analise a mensagem considerando o contexto. Não responda ao paciente.
Retorne SOMENTE JSON válido.

CONTEXTO ATUAL:
{json.dumps(estado_resumido, ensure_ascii=False)}

ÚLTIMA PERGUNTA DO USUÁRIO:
{contexto_conversa.get("pergunta_anterior")}

ÚLTIMA RESPOSTA DO BARTHO:
{contexto_conversa.get("resposta_anterior")}

MENSAGEM ATUAL:
{mensagem}

Classifique semanticamente o que o usuário quer AGORA.

Schema:
{{
  "tipo": "continuar_fluxo|pergunta_informativa|mudar_especialidade|nova_operacao|confirmar|negar|agradecer|encerrar|fora_escopo|nao_claro",
  "objetivo": "agendar|consultar_disponibilidade|consultar_dias|documentos_preparo|convenios|endereco|horario_funcionamento|cancelar|remarcar|especialidades|atendimento_humano|outro|null",
  "especialidade": "cardiologia|dermatologia|clinica_geral|null",
  "data": "texto informado pelo usuário ou null",
  "horario": "HH:MM|manha|tarde|null",
  "resposta_curta": "sim|nao|null"
}}

Regras:
- Interprete significado, não palavras exatas.
- Use o contexto para resolver pronomes como ela, ele, isso, essa consulta.
- Se Bartho perguntou se pode ajudar com mais alguma coisa e o usuário disser que era somente aquilo, é encerrar.
- Se Bartho acabou de explicar algo e o usuário perguntar se é somente aquilo, é pergunta_informativa, não encerramento.
- Perguntas durante um agendamento não cancelam o agendamento automaticamente.
- "quais dias disponíveis?", "quando ela atende?", "que dias posso ir?" em contexto de especialidade significam consultar_dias.
- Se o usuário mudar claramente de assunto/operação, indique nova_operacao.
- PEDIDO DE ATENDIMENTO HUMANO TEM PRIORIDADE: se o usuário pedir, perguntar se pode, demonstrar desejo ou solicitar contato com atendente, equipe, recepção ou uma pessoa humana, use:
  tipo = "nova_operacao"
  objetivo = "atendimento_humano"
- Isso vale mesmo em perguntas como "posso falar com alguém?", "tem como falar com a equipe?" e formulações semanticamente equivalentes.
- Um pedido de atendimento humano pode acontecer como consequência da resposta anterior; use o contexto.
- Não invente especialidade, data ou horário.
""".strip()

    bruto = chamar_qwen_texto(prompt)
    if not bruto:
        return None

    # Aceita apenas o primeiro objeto JSON produzido.
    try:
        inicio = bruto.index("{")
        fim = bruto.rindex("}") + 1
        dados = json.loads(bruto[inicio:fim])
    except Exception:
        return None

    return dados


def detectar_pedido_humano_semantico(mensagem):
    """
    Segunda camada semântica para handoff.
    Só é usada quando o roteador contextual geral não escolheu atendimento_humano.
    Não depende de frases cadastradas: pergunta ao Qwen se a intenção é falar com uma pessoa.
    """
    prompt = f"""
Analise a mensagem abaixo.

O usuário está pedindo, perguntando se pode, ou demonstrando intenção de falar
com uma pessoa da Clínica Bem (atendente, equipe, recepção ou atendimento humano)?

Mensagem:
{mensagem}

Responda SOMENTE:
SIM
ou
NAO
""".strip()

    resposta = chamar_qwen_texto(prompt)
    return resposta.strip().upper().startswith("SIM")


def _tratar_pedido_atendimento_humano_sem_metricas(mensagem):
    print("\nBartho: Posso encaminhar sua solicitação para um atendente humano?")
    resposta = ler_entrada_usuario("Você: ").strip()

    decisao = interpretar_turno_contextual(resposta)

    confirmou = bool(
        decisao
        and (
            decisao.get("tipo") == "confirmar"
            or decisao.get("resposta_curta") == "sim"
        )
    )

    if not confirmou:
        print("\nBartho: Tudo bem. O atendimento humano não foi solicitado.")
        return True

    try:
        registrar_atendimento_humano(
            mensagem,
            "solicitacao_explicita_do_usuario"
        )
        print(
            "\nBartho: Certo. Sua solicitação foi registrada para atendimento humano."
        )
        print("Bartho: Status: aguardando atendente.")
    except Exception:
        print(
            "\nBartho: Não consegui registrar a solicitação agora. "
            "Por favor, tente novamente."
        )

    return True


def tratar_pedido_atendimento_humano(*args, **kwargs):
    resultado = _tratar_pedido_atendimento_humano_sem_metricas(*args, **kwargs)
    registrar_handoff("solicitado_pelo_usuario")
    return resultado


def responder_informacao_contextual(mensagem, decisao):
    objetivo = decisao.get("objetivo")
    especialidade = decisao.get("especialidade") or especialidade_da_sessao()

    if objetivo == "atendimento_humano":
        return tratar_pedido_atendimento_humano(mensagem)

    if objetivo == "documentos_preparo":
        interpretacao = {"especialidade": especialidade or "nao_informado"}
        return responder_documentos_consulta(mensagem, interpretacao)

    if objetivo == "convenios":
        return responder_pergunta_convenio(mensagem)

    if objetivo == "consultar_dias" and especialidade:
        return responder_dias_especialidade(especialidade)

    # Demais perguntas informativas continuam abertas via RAG.
    return responder_com_rag(mensagem, especialidade=especialidade)


def classificar_fechamento(mensagem):
    """
    Classificação leve de encerramento.
    Não chama o LLM em toda mensagem, reduzindo a latência.
    """
    texto = normalizar_mensagem_curta(mensagem)
    palavras = set(texto.split())

    if not texto:
        return "CONTINUAR"

    despedidas = {"tchau", "ate", "falou"}
    agradecimentos = {"obrigado", "obrigada", "valeu", "agradeco"}

    # Encerramentos explícitos.
    if "tchau" in palavras or texto in {"ate mais", "pode encerrar", "podemos encerrar"}:
        return "ENCERRAR"

    if "era so isso" in texto or "nao preciso de mais nada" in texto:
        return "ENCERRAR"

    if "nao" in palavras and palavras.intersection(agradecimentos):
        return "ENCERRAR"

    # Agradecimento/sinal de conclusão do assunto, sem encerrar a sessão.
    if palavras.intersection(agradecimentos):
        return "AGRADECER"

    return "CONTINUAR"


def mensagem_tem_saudacao(mensagem):
    """
    Confirma saudação pelo texto real, sem confiar cegamente no campo
    'saudacao' produzido pelo LLM.
    """
    texto = normalizar_mensagem_curta(mensagem)
    palavras = set(texto.split())

    if palavras.intersection({"oi", "ola", "opa"}):
        return True

    expressoes = (
        "bom dia",
        "boa tarde",
        "boa noite"
    )

    return any(expr in texto for expr in expressoes)


def pergunta_sobre_dias_atendimento(mensagem):
    texto = normalizar_mensagem_curta(mensagem)

    menciona_dia = any(
        termo in texto
        for termo in (
            "quais dias",
            "que dias",
            "qual dia",
            "quando atende",
            "dias atende",
            "dias de atendimento"
        )
    )

    return menciona_dia


def responder_dias_especialidade(especialidade):
    medicos = consultar_medicos_por_especialidade(especialidade)

    if not medicos:
        return False

    medico = medicos[0]
    dias = consultar_dias_medico(medico)
    dias_texto = formatar_lista_dias(nomes_dias(dias))

    print(f"\nBartho: {medico} atende às {dias_texto}s.")
    return True


def classificar_orientacao_segura(mensagem):
    """Classifica situações de saúde sem produzir diagnóstico."""
    prompt = f"""
Você é apenas um classificador de segurança do Bartho, assistente administrativo da Clínica Bem.
Não responda ao paciente e não faça diagnóstico.

MENSAGEM:
{mensagem}

Retorne SOMENTE JSON válido:
{{
  "categoria": "sintoma" | "pedido_diagnostico" | "pedido_medicamento" | "possivel_urgencia" | "outro"
}}

Regras:
- sintoma: a pessoa relata dor, mal-estar, mancha, coceira ou outro sintoma sem sinais claros de urgência.
- pedido_diagnostico: pergunta o que tem, qual doença pode ser ou pede avaliação/diagnóstico.
- pedido_medicamento: pede remédio, dose, tratamento ou o que deve tomar.
- possivel_urgencia: relato que pode precisar de atendimento imediato, como dor muito forte/intensa, falta de ar importante, desmaio, sangramento intenso, convulsão ou pedido explícito de emergência/urgência. Em dúvida entre sintoma e urgência, escolha possivel_urgencia.
- outro: perguntas administrativas, preço, convênio, cobertura, serviços, endereço, documentos, agenda ou qualquer outro assunto.
""".strip()
    bruto = chamar_qwen_texto(prompt)
    if not bruto:
        return "outro"
    try:
        inicio = bruto.index("{")
        fim = bruto.rindex("}") + 1
        dados = json.loads(bruto[inicio:fim])
        categoria = str(dados.get("categoria", "outro")).strip()
    except Exception:
        return "outro"
    permitidas = {"sintoma", "pedido_diagnostico", "pedido_medicamento", "possivel_urgencia", "outro"}
    return categoria if categoria in permitidas else "outro"


def responder_orientacao_segura(mensagem):
    """Orienta sem diagnosticar, prescrever ou escolher especialidade pelo paciente."""
    categoria = classificar_orientacao_segura(mensagem)
    if categoria == "outro":
        return False
    if categoria == "possivel_urgencia":
        resposta = (
            "Não consigo avaliar sintomas ou realizar diagnóstico. Como o relato pode precisar de "
            "avaliação imediata, procure um serviço de urgência/emergência. A Clínica Bem realiza "
            "consultas, mas não substitui atendimento de emergência."
        )
    elif categoria == "pedido_medicamento":
        resposta = (
            "Não posso indicar medicamentos, doses ou tratamentos. Isso precisa ser definido por "
            "um profissional de saúde após avaliação. A Clínica Bem oferece consultas de Cardiologia, "
            "Clínica Geral e Dermatologia; se quiser, posso ajudar a consultar a disponibilidade."
        )
    elif categoria == "pedido_diagnostico":
        resposta = (
            "Não consigo realizar diagnóstico ou determinar a causa de sintomas. A Clínica Bem oferece "
            "consultas de Cardiologia, Clínica Geral e Dermatologia. Se quiser, posso ajudar a consultar "
            "a disponibilidade dessas especialidades."
        )
    else:
        resposta = (
            "Não consigo avaliar sintomas ou fazer diagnóstico. A Clínica Bem oferece consultas de "
            "Cardiologia, Clínica Geral e Dermatologia. Se quiser, posso ajudar a consultar a disponibilidade. "
            "Se os sintomas forem intensos ou você acreditar que seja uma emergência, procure um serviço "
            "de urgência/emergência."
        )
    print(f"\nBartho: {resposta}")
    registrar_contexto("orientacao_segura", mensagem, resposta)
    return True


def extrair_plano_texto(mensagem):
    texto = normalizar_mensagem_curta(mensagem)
    planos = {
        "unimed": "Unimed",
        "bradesco saude": "Bradesco Saúde",
        "sulamerica": "SulAmérica",
        "amil": "Amil",
    }
    return next((nome for chave, nome in planos.items() if chave in texto), None)


def servicos_nao_oferecidos_mencionados(mensagem):
    """Extrai todos os serviços explicitamente fora do escopo da Clínica Bem."""
    texto = normalizar_mensagem_curta(mensagem)
    encontrados = []
    if "cirurgia" in texto or "cirurgias" in texto:
        encontrados.append("cirurgias")
    if "exame de sangue" in texto or "exames de sangue" in texto:
        encontrados.append("exames de sangue")
    return encontrados


def _responder_servico_nao_oferecido_sem_metricas(mensagem):
    servicos = servicos_nao_oferecidos_mencionados(mensagem)
    if not servicos:
        return False

    if len(servicos) == 1:
        lista = servicos[0]
    else:
        lista = ", ".join(servicos[:-1]) + " nem " + servicos[-1]

    resposta = (
        f"A Clínica Bem não realiza {lista}. Como esses serviços não são oferecidos pela clínica, "
        "não consigo afirmar se o seu convênio os cobre em outros locais."
    )
    print(f"\nBartho: {resposta}")
    registrar_contexto("servicos", mensagem, resposta)
    return True


def responder_servico_nao_oferecido(*args, **kwargs):
    resultado = _responder_servico_nao_oferecido_sem_metricas(*args, **kwargs)
    if resultado:
        registrar_resolucao("servico_nao_oferecido", orientacao=True)
    return resultado


def responder_valor_consulta(mensagem):
    """Rota rápida para perguntas claras de preço, sem chamar o Qwen."""
    if pergunta_sobre_convenio(mensagem):
        return False
    texto = normalizar_mensagem_curta(mensagem)
    termos_preco = (
        "quanto custa", "qual o valor", "qual valor", "preco da consulta",
        "preco de uma consulta", "valor da consulta", "valor de uma consulta",
        "custo da consulta", "custo de uma consulta", "quanto pago", "quanto vou pagar"
    )
    if not any(termo in texto for termo in termos_preco):
        return False
    contexto = ler_secoes_base(["VALORES"])
    if not contexto:
        return False
    resposta = "A consulta particular custa R$ 70,00."
    print(f"\nBartho: {resposta}")
    registrar_contexto("valores", mensagem, resposta)
    registrar_rag()
    registrar_resolucao("preco")
    return True


def pergunta_sobre_convenio(mensagem):
    texto = normalizar_mensagem_curta(mensagem)
    termos = ("convenio", "unimed", "bradesco saude", "sulamerica", "amil", "plano")
    return any(termo in texto for termo in termos)


def pergunta_sobre_cobertura(mensagem):
    texto = normalizar_mensagem_curta(mensagem)
    return any(t in texto for t in ("cobre", "cobertura", "coberto", "coberta"))


def responder_convenio_e_servicos(mensagem):
    """Compõe convênio + cobertura + múltiplos serviços sem encerrar no primeiro match."""
    texto = normalizar_mensagem_curta(mensagem)
    plano = extrair_plano_texto(mensagem)
    assunto_convenio = contexto_conversa.get("assunto_atual") == "convenios"
    plano_contexto = contexto_conversa.get("plano_atual") if assunto_convenio else None
    plano_efetivo = plano or plano_contexto
    servicos = servicos_nao_oferecidos_mencionados(mensagem)
    cobertura = pergunta_sobre_cobertura(mensagem)
    pergunta_convenio = pergunta_sobre_convenio(mensagem)

    # Continuação curta como "cobre tudo?" herda somente o tópico/plano de convênio.
    if not pergunta_convenio and not (assunto_convenio and (cobertura or servicos)):
        return False

    aceitos = {"Unimed", "Bradesco Saúde", "SulAmérica"}
    partes = []

    pergunta_lista = any(t in texto for t in (
        "quais convenios", "quais planos", "que convenios", "que planos",
        "convenios aceitos", "planos aceitos", "aceita quais"
    ))
    pergunta_aceite = any(t in texto for t in (
        "aceita", "aceitam", "atende", "atendem", "trabalha com", "tem convenio"
    ))

    if pergunta_lista or ("convenio" in texto and not plano_efetivo and pergunta_aceite):
        partes.append("A Clínica Bem aceita Unimed, Bradesco Saúde e SulAmérica. Também realiza consultas particulares.")
    elif plano_efetivo:
        if plano_efetivo in aceitos:
            if plano or pergunta_aceite:
                partes.append(f"A Clínica Bem aceita {plano_efetivo}.")
            if cobertura:
                partes.append(
                    "A consulta pode ser coberta conforme as condições do seu plano, mas não consigo garantir "
                    "a cobertura individual do seu contrato nem que ele cubra todos os serviços."
                )
        elif plano:
            partes.append("A Clínica Bem aceita Unimed, Bradesco Saúde e SulAmérica; esse plano não consta entre os convênios aceitos.")
    elif cobertura and assunto_convenio:
        partes.append(
            "A cobertura depende das condições do plano e não consigo garantir a cobertura individual do contrato."
        )

    if servicos:
        if len(servicos) == 1:
            lista = servicos[0]
        else:
            lista = ", ".join(servicos[:-1]) + " nem " + servicos[-1]
        partes.append(
            f"A Clínica Bem não realiza {lista}; por isso, não consigo informar se esses serviços são cobertos "
            "pelo convênio em outros locais."
        )

    if not partes:
        return False

    resposta = " ".join(partes)
    print(f"\nBartho: {resposta}")
    registrar_contexto("convenios", mensagem, resposta, plano=plano_efetivo)
    registrar_rag()
    registrar_resolucao("convenio")
    if servicos:
        registrar_resolucao("servico_nao_oferecido", orientacao=True)
    return True


def _responder_pergunta_convenio_sem_metricas(mensagem):
    return responder_convenio_e_servicos(mensagem)


def responder_pergunta_convenio(mensagem):
    # A métrica já é registrada pela resposta composta para não duplicar eventos.
    return responder_convenio_e_servicos(mensagem)


def responder_informacao_clinica_rapida(mensagem):
    """FAQ administrativa objetiva resolvida por banco/regras, antes do LLM."""
    texto = normalizar_mensagem_curta(mensagem)

    # Horário de funcionamento: responder por regra determinística.
    # Isso evita depender do classificador ou do RAG para um fato fixo da base.
    termos_horario_funcionamento = (
        "horario de funcionamento", "horarios de funcionamento",
        "que horas funciona", "qual horario funciona", "qual o horario",
        "horario da clinica", "horarios da clinica",
        "que horas abre", "a que horas abre", "que horas fecha",
        "a que horas fecha", "abre que horas", "fecha que horas",
        "funciona ate que horas", "funciona de que horas",
        "qual o horario de atendimento", "horario de atendimento",
        "horarios de atendimento", "quando a clinica funciona",
    )
    pergunta_sobre_horario = any(t in texto for t in termos_horario_funcionamento)
    if pergunta_sobre_horario:
        contexto_horario = ler_secoes_base(["HORÁRIO DE FUNCIONAMENTO"])
        if contexto_horario:
            resposta = (
                "A Clínica Bem funciona de segunda a sexta-feira, das 08h às 18h. "
                "O último horário de consulta começa às 17h."
            )
            print(f"\nBartho: {resposta}")
            registrar_contexto("horario_funcionamento", mensagem, resposta)
            registrar_resolucao("informacao_clinica")
            return True
        return False

    termos_lista_especialidades = (
        "quais especialidades", "que especialidades", "especialidades atendem",
        "especialidades voces atendem", "especialidades tem", "especialidades possuem"
    )
    if any(t in texto for t in termos_lista_especialidades):
        especialidades = consultar_especialidades()
        if not especialidades:
            return False
        nomes = [nome_especialidade(e) for e in especialidades]
        resposta = "Atualmente a Clínica Bem atende " + ", ".join(nomes) + "."
        print(f"\nBartho: {resposta}")
        registrar_contexto("especialidades", mensagem, resposta)
        registrar_resolucao("informacao_clinica")
        return True

    especialidade = extrair_especialidade_texto(mensagem)
    termos_dias = (
        "quais dias", "que dias", "qual dia", "dias atende", "dias atendem",
        "quando atende", "quando atendem"
    )
    if especialidade and any(t in texto for t in termos_dias):
        medicos = consultar_medicos_por_especialidade(especialidade)
        if not medicos:
            return False
        medico = medicos[0]
        dias = consultar_dias_medico(medico)
        dias_texto = formatar_lista_dias(nomes_dias(dias))
        resposta = f"{medico} atende às {dias_texto}s."
        print(f"\nBartho: {resposta}")
        registrar_contexto("dias_atendimento", mensagem, resposta, especialidade=especialidade)
        registrar_resolucao("informacao_clinica")
        return True

    return False



def mensagem_operacional_explicita(mensagem):
    """Novo pedido operacional claro não precisa de um classificador contextual antes do interpretador principal."""
    texto = normalizar_mensagem_curta(mensagem)
    termos = (
        "agendar", "agendamento", "marcar consulta", "marcar uma consulta",
        "remarcar", "remarcacao", "cancelar consulta", "cancelar minha consulta",
        "ver disponibilidade", "tem horario", "tem horarios", "horarios disponiveis",
        "disponivel amanha", "disponivel segunda", "disponivel terca",
        "disponivel quarta", "disponivel quinta", "disponivel sexta"
    )
    return any(termo in texto for termo in termos)


def responder_administrativo_sem_inventar(mensagem):
    """Abstém de forma rápida em fatos administrativos que não existem na base autorizada."""
    texto = normalizar_mensagem_curta(mensagem)
    topicos_sem_base = (
        "estacionamento", "estacionar", "parking", "wifi", "wi fi",
        "cafeteria", "lanchonete", "elevador", "acessibilidade",
        "forma de pagamento", "formas de pagamento", "aceita pix", "aceita cartao"
    )
    if not any(t in texto for t in topicos_sem_base):
        return False

    resposta = (
        "Não tenho essa informação confirmada na base da Clínica Bem e não quero inventar. "
        "Posso ajudar com as informações cadastradas da clínica, como consultas, especialidades, "
        "horários, convênios, documentos e agendamentos."
    )
    print(f"\nBartho: {resposta}")
    registrar_contexto("informacao_nao_disponivel", mensagem, resposta)
    registrar_resolucao("informacao_rag", orientacao=True)
    return True

def processar_mensagem(
    mensagem,
    permitir_esclarecimento=True
):
    """
    Roteamento principal com precedência explícita.

    Princípio:
    - rotas específicas e seguras não podem ser interceptadas por classificadores genéricos;
    - o LLM contextual é usado quando o contexto realmente é necessário;
    - handoff semântico é fallback, não primeira barreira.
    """

    texto_normalizado = normalizar_mensagem_curta(mensagem)

    # --------------------------------------------------
    # 1. ENCERRAMENTO / AGRADECIMENTO EXPLÍCITO
    # --------------------------------------------------
    # Regra determinística: recusar atendimento humano e se despedir
    # nunca pode ser interpretado como confirmação de handoff pelo Qwen.
    texto_fechamento = normalizar_mensagem_curta(mensagem)
    recusas_handoff_com_despedida = {
        "nao precisa tchau", "nao obrigado tchau", "nao obrigada tchau",
        "nao quero tchau", "nao precisa ate mais", "nao obrigado ate mais",
        "nao obrigada ate mais",
    }
    if texto_fechamento in recusas_handoff_com_despedida:
        contexto_conversa["pendencia"] = None
        contexto_conversa["aguardando_mais_alguma_coisa"] = False
        print("\nBartho: Tudo bem! Até mais! 👋")
        return "encerrar"

    fechamento = classificar_fechamento(mensagem)

    if fechamento == "ENCERRAR":
        contexto_conversa["pendencia"] = None
        contexto_conversa["aguardando_mais_alguma_coisa"] = False
        print("\nBartho: Tudo bem! Até mais! 👋")
        return "encerrar"

    if fechamento == "AGRADECER":
        contexto_conversa["aguardando_mais_alguma_coisa"] = True
        print("\nBartho: Por nada! Posso ajudar com mais alguma coisa?")
        return "aguardando_novo_assunto"

    # --------------------------------------------------
    # 2. SAUDAÇÃO PURA
    # --------------------------------------------------
    # Evita chamar classificadores genéricos para um caso simples e conhecido.
    saudacoes_puras = {
        "oi",
        "ola",
        "opa",
        "bom dia",
        "boa tarde",
        "boa noite",
        "oi tudo bem",
        "ola tudo bem"
    }

    if texto_normalizado in saudacoes_puras:
        if not contexto_conversa["saudacao_realizada"]:
            exibir_saudacao()
            contexto_conversa["saudacao_realizada"] = True

        resposta = ler_entrada_usuario(
            "\nBartho: Como posso ajudar?\n"
            "Você: "
        ).strip()

        if resposta:
            return processar_mensagem(resposta)

        return

    # --------------------------------------------------
    # 3. PENDÊNCIAS EXPLÍCITAS DO SISTEMA
    # --------------------------------------------------
    # Pendência de documentos: a resposta pode ser somente a especialidade.
    if contexto_conversa.get("pendencia") == "especialidade_documentos":
        interpretacao_pendencia = interpretar_e_corrigir(mensagem)

        if tentar_resolver_pendencia(
            mensagem,
            interpretacao_pendencia
        ):
            return

    # Oferta anterior de atendimento humano.
    if contexto_conversa.get("pendencia") == "oferta_handoff":
        decisao_pendencia = interpretar_turno_contextual(mensagem)

        if decisao_pendencia:
            tipo = decisao_pendencia.get("tipo")
            resposta_curta = decisao_pendencia.get("resposta_curta")

            resposta_normalizada = normalizar_mensagem_curta(mensagem)
            recusou_handoff = (
                resposta_normalizada in {
                    "nao", "nao precisa", "nao precisa obrigado", "nao precisa obrigada",
                    "nao quero", "nao quero obrigado", "nao quero obrigada",
                    "nao obrigado", "nao obrigada", "nao precisa tchau",
                    "nao obrigado tchau", "nao obrigada tchau", "nao quero tchau",
                    "nao precisa ate mais", "nao obrigado ate mais", "nao obrigada ate mais",
                }
                or (
                    resposta_normalizada.startswith("nao ")
                    and any(p in resposta_normalizada for p in (
                        "tchau", "ate mais", "precisa", "quero", "obrigado", "obrigada"
                    ))
                )
            )

            if recusou_handoff:
                contexto_conversa["pendencia"] = None
                print("\nBartho: Tudo bem. O atendimento humano não foi solicitado.")
                if "tchau" in resposta_normalizada or "ate mais" in resposta_normalizada:
                    print("Bartho: Até mais! 👋")
                    return "encerrar"
                return

            if tipo == "confirmar" or resposta_curta == "sim":
                pergunta_original = (
                    contexto_conversa.get("pergunta_anterior")
                    or mensagem
                )
                contexto_conversa["pendencia"] = None
                tratar_pedido_atendimento_humano(pergunta_original)
                return

            # "não" sozinho rejeita a oferta.
            if (
                (tipo == "negar" or resposta_curta == "nao")
                and len(texto_normalizado.split()) <= 3
            ):
                contexto_conversa["pendencia"] = None
                print(
                    "\nBartho: Tudo bem. "
                    "O atendimento humano não foi solicitado."
                )
                return

            # Se a pessoa escreveu um novo pedido junto com a negativa,
            # abandonamos a oferta antiga e processamos o pedido atual.
            contexto_conversa["pendencia"] = None

    # --------------------------------------------------
    # 4. CONTINUAÇÕES DETERMINÍSTICAS DE FAQ
    # --------------------------------------------------
    # Ex.: depois de falar de convênios, "quais?" continua sendo convênios.
    if (
        contexto_conversa.get("assunto_atual") == "convenios"
        and texto_normalizado in {
            "quais",
            "quais sao",
            "quais convenios",
            "quais planos",
            "e quais",
            "e quais sao"
        }
    ):
        contexto = ler_secoes_base(["CONVÊNIOS"])

        if contexto:
            linhas = [
                linha.strip()
                for linha in contexto.splitlines()
                if linha.strip()
            ]

            planos = None

            for indice, linha in enumerate(linhas):
                if "seguintes convenios" in normalizar_mensagem_curta(linha):
                    if indice + 1 < len(linhas):
                        planos = linhas[indice + 1].rstrip(".")
                    break

            if planos:
                resposta = f"A Clínica Bem aceita {planos}."
                print(f"\nBartho: {resposta}")
                registrar_contexto(
                    "convenios",
                    mensagem,
                    resposta
                )
                return

    # --------------------------------------------------
    # 5. FAQS ESPECÍFICAS
    # --------------------------------------------------
    # Essas rotas têm fonte própria e não devem ser interceptadas por handoff.
    # Serviços explicitamente não oferecidos vêm antes de preço/convênio,
    # inclusive em continuações como "e cobre cirurgia também?".
    if responder_convenio_e_servicos(mensagem):
        return

    if responder_servico_nao_oferecido(mensagem):
        return

    if responder_informacao_clinica_rapida(mensagem):
        return

    if responder_valor_consulta(mensagem):
        return

    if pergunta_sobre_documentos_ou_preparo(mensagem):
        interpretacao_docs = {
            "especialidade": (
                extrair_especialidade_texto(mensagem)
                or "nao_informado"
            )
        }

        if responder_documentos_consulta(
            mensagem,
            interpretacao_docs
        ):
            return

    if responder_administrativo_sem_inventar(mensagem):
        return

    # --------------------------------------------------
    # 5.5. REFERÊNCIA AO CONJUNTO DE ESPECIALIDADES APRESENTADO
    # --------------------------------------------------
    # Quando a clínica acabou de apresentar as especialidades e o usuário
    # escolhe o conjunto (ex.: "quero as três" / "quero todas"), não há
    # ambiguidade que justifique Qwen nem handoff. A própria conversa já
    # define quais são as opções disponíveis.
    especialidades_referidas = extrair_especialidades_explicitas(mensagem)
    marcadores_escolha = (
        "quero", "gostaria", "preciso", "agendar", "marcar", "escolho"
    )

    if (
        contexto_conversa.get("assunto_atual") == "especialidades"
        and len(especialidades_referidas) > 1
        and any(marcador in texto_normalizado for marcador in marcadores_escolha)
    ):
        contexto_conversa["aguardando_mais_alguma_coisa"] = False
        contexto_conversa["pendencia"] = None

        interpretacao_multipla = {
            "saudacao": False,
            "intencao": "agendar",
            "especialidade": especialidades_referidas[0],
            "especialidades": especialidades_referidas,
            "consultas": [
                {
                    "especialidade": especialidade,
                    "data": "nao_informado",
                    "horario": "nao_informado"
                }
                for especialidade in especialidades_referidas
            ],
            "data": "nao_informado",
            "horario": "nao_informado"
        }

        fluxo_agendamentos_multiplos(
            mensagem,
            interpretacao_multipla
        )
        return

    # --------------------------------------------------
    # 6. CONTEXTO CONVERSACIONAL
    # --------------------------------------------------
    # Aqui entram pronomes, perguntas laterais e referências ao turno anterior.
    decisao_contextual = None
    if tem_contexto_conversacional_relevante() and not mensagem_operacional_explicita(mensagem):
        decisao_contextual = interpretar_turno_contextual(mensagem)

    if decisao_contextual:
        tipo = decisao_contextual.get("tipo")
        objetivo = decisao_contextual.get("objetivo")

        if tipo == "encerrar":
            print("\nBartho: Tudo bem! Até mais! 👋")
            return "encerrar"

        if tipo == "agradecer":
            contexto_conversa["aguardando_mais_alguma_coisa"] = True
            print("\nBartho: Por nada! Posso ajudar com mais alguma coisa?")
            return "aguardando_novo_assunto"

        if objetivo == "atendimento_humano":
            tratar_pedido_atendimento_humano(mensagem)
            return

        if tipo == "pergunta_informativa":
            if responder_informacao_contextual(
                mensagem,
                decisao_contextual
            ):
                return

        if (
            contexto_conversa.get("aguardando_mais_alguma_coisa")
            and tipo not in {"confirmar", "negar"}
        ):
            contexto_conversa["aguardando_mais_alguma_coisa"] = False

    # Continuação curta de documentos/preparo já respondidos.
    if responder_continuacao_contextual(mensagem):
        return

    # --------------------------------------------------
    # 7. INTERPRETADOR OPERACIONAL PRINCIPAL
    # --------------------------------------------------
    interpretacao = interpretar_e_corrigir(mensagem)

    saudacao = (
        interpretacao.get("saudacao", False)
        and mensagem_tem_saudacao(mensagem)
    )

    intencao = interpretacao["intencao"]

    if saudacao and not contexto_conversa["saudacao_realizada"]:
        exibir_saudacao()
        contexto_conversa["saudacao_realizada"] = True

    # Se a mensagem combinou saudação com uma FAQ, as FAQs já foram
    # verificadas acima. Aqui seguimos com a intenção operacional.

    consulta_sem_dados_operacionais = (
        intencao == "consultar"
        and interpretacao.get("especialidade")
        in [None, "nao_informado"]
        and interpretacao.get("data")
        in [None, "nao_informado"]
        and interpretacao.get("horario")
        in [None, "nao_informado"]
    )

    if consulta_sem_dados_operacionais:
        if responder_com_rag(mensagem):
            return
        if responder_orientacao_segura(mensagem):
            return

    if intencao == "listar_especialidades":
        fluxo_especialidades()
        return

    if intencao == "consultar_medico":
        fluxo_consultar_medico(interpretacao)
        return

    if intencao == "agendar":
        decisao = decidir_tool(
            interpretacao,
            mensagem
        )

        especialidades_interpretadas = interpretacao.get("especialidades", [])
        especialidades_tool = decisao.get("argumentos", {}).get("especialidades", [])
        especialidades = []
        for item in list(especialidades_interpretadas or []) + list(especialidades_tool or []):
            if item in {"cardiologia", "dermatologia", "clinica_geral"} and item not in especialidades:
                especialidades.append(item)

        if len(especialidades) > 1:
            fluxo_agendamentos_multiplos(
                mensagem,
                interpretacao
            )
        else:
            fluxo_agendamento(
                mensagem,
                interpretacao
            )

        return

    if intencao == "consultar":
        fluxo_consulta(
            interpretacao,
            mensagem
        )
        return

    if intencao == "remarcar":
        fluxo_remarcacao(mensagem, interpretacao)
        return

    if intencao == "cancelar":
        fluxo_cancelamento(mensagem, interpretacao)
        return

    # --------------------------------------------------
    # 8. INFORMAÇÃO GERAL / RAG
    # --------------------------------------------------
    # A base tem prioridade sobre o handoff genérico.
    contextos_rag = buscar_contexto(
        mensagem,
        limite=2
    )

    if contextos_rag:
        if responder_rag_verificado(
            mensagem,
            especialidade=especialidade_da_sessao()
        ):
            return

    # Se não era uma informação factual da base, verificamos se é uma
    # situação de saúde que pode ser resolvida com orientação segura.
    if responder_orientacao_segura(mensagem):
        return

    # --------------------------------------------------
    # 9. HANDOFF SEMÂNTICO EXPLÍCITO
    # --------------------------------------------------
    # Só chega aqui se nenhuma rota conhecida resolveu a mensagem.
    if detectar_pedido_humano_semantico(mensagem):
        tratar_pedido_atendimento_humano(mensagem)
        return

    # --------------------------------------------------
    # 10. ESCLARECIMENTO E HANDOFF FINAL
    # --------------------------------------------------
    if permitir_esclarecimento:
        print(
            "\nBartho: Não consegui identificar "
            "com segurança o que você precisa."
        )

        resposta = ler_entrada_usuario(
            "Bartho: Pode me explicar de outra forma?\n"
            "Você: "
        ).strip()

        if resposta:
            return processar_mensagem(
                resposta,
                permitir_esclarecimento=False
            )

        return

    responder_sem_inventar(mensagem)


# ==================================================
# CONTINUAR ATENDIMENTO
# ==================================================

def continuar_atendimento():
    """
    Um único loop para toda a conversa.
    Não pergunta "algo mais?" depois de cada resposta.
    Essa pergunta só aparece quando o usuário demonstra que concluiu o assunto.
    """
    # METRICAS_SESSAO_INICIO
    iniciar_sessao()
    while True:
        try:
            mensagem = ler_entrada_usuario("\nVocê: ").strip()
        except (KeyboardInterrupt, EOFError):
            # METRICAS_ABANDONO
            finalizar_sessao(abandono=True)
            print()
            return

        if not mensagem:
            continue

        # METRICAS_INTERACAO
        registrar_interacao()

        if contexto_conversa["aguardando_mais_alguma_coisa"]:
            curta = normalizar_mensagem_curta(mensagem)
            confirmacoes = {"sim", "s", "claro", "pode", "pode sim"}
            negacoes = {"nao", "n", "nao obrigado", "nao obrigada"}

            if curta in negacoes:
                print("\nBartho: Tudo bem! Até mais! 👋")
                finalizar_sessao()
                return

            if curta in confirmacoes:
                contexto_conversa["aguardando_mais_alguma_coisa"] = False
                print("\nBartho: Claro! Do que você precisa?")
                continue

            # "sim, quero remarcar", "sim, quanto custa?" etc. são pedidos completos.
            contexto_conversa["aguardando_mais_alguma_coisa"] = False

        resultado = processar_mensagem(mensagem)

        if resultado == "encerrar":
            # METRICAS_ENCERRAMENTO
            finalizar_sessao()
            return


# ==================================================
# INICIAR BANCO
# ==================================================

criar_banco()
cadastrar_medicos()
cadastrar_dias_atendimento()


# ==================================================
# INÍCIO DO BARTHO
# ==================================================

if __name__ == "__main__":
    print("\n==============================")
    print("           BARTHO")
    print("==============================")
    print("Assistente virtual da Clínica Bem")
    print()
    continuar_atendimento()
