import json

from teste_llm import interpretar_mensagem

from tools import (
    tool_listar_especialidades,
    tool_consultar_medicos,
    tool_consultar_dias_medico,
    tool_consultar_horarios,
    tool_buscar_agendamentos,
    tool_encaminhar_humano
)


# ==================================================
# OBTER ESPECIALIDADES
# ==================================================

def obter_especialidades(interpretacao):
    """
    Retorna todas as especialidades identificadas
    pelo LLM.

    Mantém compatibilidade com o campo antigo
    'especialidade'.
    """

    especialidades = interpretacao.get(
        "especialidades",
        []
    )

    if not isinstance(especialidades, list):
        especialidades = []

    especialidades_validas = [
        "cardiologia",
        "dermatologia",
        "clinica_geral"
    ]

    resultado = []

    for especialidade in especialidades:

        if (
            especialidade in especialidades_validas
            and especialidade not in resultado
        ):
            resultado.append(especialidade)

    # Compatibilidade com interpretações antigas
    # que tenham apenas "especialidade".
    especialidade_principal = interpretacao.get(
        "especialidade",
        "nao_informado"
    )

    if (
        especialidade_principal in especialidades_validas
        and especialidade_principal not in resultado
    ):
        resultado.insert(
            0,
            especialidade_principal
        )

    return resultado


# ==================================================
# AGENTE: ESCOLHER TOOL
# ==================================================

def decidir_tool(
    interpretacao,
    mensagem_original
):

    intencao = interpretacao.get(
        "intencao",
        "nao_informado"
    )

    especialidades = obter_especialidades(
        interpretacao
    )

    especialidade = (
        especialidades[0]
        if especialidades
        else "nao_informado"
    )

    data = interpretacao.get(
        "data",
        "nao_informado"
    )

    horario = interpretacao.get(
        "horario",
        "nao_informado"
    )

    # ----------------------------------------------
    # LISTAR ESPECIALIDADES
    # ----------------------------------------------

    if intencao == "listar_especialidades":

        return {
            "tool": "listar_especialidades",
            "argumentos": {},
            "faltando": [],
            "requer_confirmacao": False
        }

    # ----------------------------------------------
    # CONSULTAR MÉDICO / ESPECIALIDADE
    # ----------------------------------------------

    if intencao == "consultar_medico":

        if not especialidades:

            return {
                "tool": "consultar_medicos",
                "argumentos": {},
                "faltando": [
                    "especialidade"
                ],
                "requer_confirmacao": False
            }

        return {
            "tool": "consultar_medicos",
            "argumentos": {
                "especialidade": especialidade,
                "especialidades": especialidades
            },
            "faltando": [],
            "requer_confirmacao": False
        }

    # ----------------------------------------------
    # AGENDAR
    # ----------------------------------------------

    if intencao == "agendar":

        argumentos = {}
        faltando = []

        if especialidades:

            argumentos[
                "especialidade"
            ] = especialidade

            argumentos[
                "especialidades"
            ] = especialidades

        else:

            faltando.append(
                "especialidade"
            )

        if (
            data
            and data != "nao_informado"
        ):

            argumentos[
                "data"
            ] = data

        else:

            faltando.append(
                "data"
            )

        if (
            horario
            and horario != "nao_informado"
        ):

            argumentos[
                "horario"
            ] = horario

        else:

            faltando.append(
                "horario"
            )

        # Médico e paciente são resolvidos
        # pelo fluxo conversacional do Bartho.
        faltando.append(
            "medico"
        )

        faltando.append(
            "paciente"
        )

        return {
            "tool": "criar_agendamento",
            "argumentos": argumentos,
            "faltando": faltando,
            "requer_confirmacao": True,
            "quantidade_consultas": len(
                especialidades
            )
        }

    # ----------------------------------------------
    # CONSULTAR DISPONIBILIDADE
    # ----------------------------------------------

    if intencao == "consultar":

        argumentos = {}
        faltando = []

        if especialidades:

            argumentos[
                "especialidade"
            ] = especialidade

            argumentos[
                "especialidades"
            ] = especialidades

        if (
            data
            and data != "nao_informado"
        ):

            argumentos[
                "data"
            ] = data

        if (
            horario
            and horario != "nao_informado"
        ):

            argumentos[
                "horario"
            ] = horario

        return {
            "tool": "consultar_horarios",
            "argumentos": argumentos,
            "faltando": faltando,
            "requer_confirmacao": False
        }

    # ----------------------------------------------
    # CANCELAR
    # ----------------------------------------------

    if intencao == "cancelar":

        return {
            "tool": "buscar_agendamentos",
            "argumentos": {},
            "faltando": [
                "paciente"
            ],
            "proxima_acao": (
                "cancelar_agendamento"
            ),
            "requer_confirmacao": True
        }

    # ----------------------------------------------
    # REMARCAR
    # ----------------------------------------------

    if intencao == "remarcar":

        return {
            "tool": "buscar_agendamentos",
            "argumentos": {},
            "faltando": [
                "paciente"
            ],
            "proxima_acao": (
                "remarcar_agendamento"
            ),
            "requer_confirmacao": True
        }

    # ----------------------------------------------
    # FORA DO ESCOPO
    # ----------------------------------------------

    return {
        "tool": "encaminhar_humano",
        "argumentos": {
            "mensagem": mensagem_original,
            "motivo": "assunto_fora_do_escopo"
        },
        "faltando": [],
        "requer_confirmacao": True
    }


# ==================================================
# EXECUTAR TOOL SEGURA
# ==================================================

def executar_tool(decisao):

    nome_tool = decisao.get(
        "tool"
    )

    argumentos = decisao.get(
        "argumentos",
        {}
    )

    faltando = decisao.get(
        "faltando",
        []
    )

    # ----------------------------------------------
    # SE FALTAM DADOS
    # ----------------------------------------------

    if faltando:

        return {
            "executado": False,
            "tool": nome_tool,
            "faltando": faltando,
            "requer_confirmacao": decisao.get(
                "requer_confirmacao",
                False
            )
        }

    try:

        # ------------------------------------------
        # LISTAR ESPECIALIDADES
        # ------------------------------------------

        if nome_tool == "listar_especialidades":

            resultado = (
                tool_listar_especialidades()
            )

        # ------------------------------------------
        # CONSULTAR MÉDICOS
        # ------------------------------------------

        elif nome_tool == "consultar_medicos":

            if (
                "especialidade"
                not in argumentos
            ):

                return {
                    "executado": False,
                    "tool": nome_tool,
                    "faltando": [
                        "especialidade"
                    ]
                }

            resultado = (
                tool_consultar_medicos(
                    argumentos[
                        "especialidade"
                    ]
                )
            )

        # ------------------------------------------
        # CONSULTAR DIAS DO MÉDICO
        # ------------------------------------------

        elif nome_tool == "consultar_dias_medico":

            if "medico" not in argumentos:

                return {
                    "executado": False,
                    "tool": nome_tool,
                    "faltando": [
                        "medico"
                    ]
                }

            resultado = (
                tool_consultar_dias_medico(
                    argumentos[
                        "medico"
                    ]
                )
            )

        # ------------------------------------------
        # CONSULTAR HORÁRIOS
        # ------------------------------------------

        elif nome_tool == "consultar_horarios":

            faltando_consulta = []

            if (
                "medico"
                not in argumentos
            ):

                faltando_consulta.append(
                    "medico"
                )

            if (
                "data"
                not in argumentos
            ):

                faltando_consulta.append(
                    "data"
                )

            if faltando_consulta:

                return {
                    "executado": False,
                    "tool": nome_tool,
                    "faltando": (
                        faltando_consulta
                    )
                }

            resultado = (
                tool_consultar_horarios(
                    argumentos[
                        "medico"
                    ],
                    argumentos[
                        "data"
                    ]
                )
            )

        # ------------------------------------------
        # BUSCAR AGENDAMENTOS
        # ------------------------------------------

        elif nome_tool == "buscar_agendamentos":

            if (
                "paciente"
                not in argumentos
            ):

                return {
                    "executado": False,
                    "tool": nome_tool,
                    "faltando": [
                        "paciente"
                    ]
                }

            resultado = (
                tool_buscar_agendamentos(
                    argumentos[
                        "paciente"
                    ]
                )
            )

        # ------------------------------------------
        # ENCAMINHAR PARA HUMANO
        # ------------------------------------------

        elif nome_tool == "encaminhar_humano":

            # O agente não transfere sozinho.
            # O usuário precisa confirmar
            # no fluxo conversacional.
            return {
                "executado": False,
                "tool": nome_tool,
                "requer_confirmacao": True,
                "argumentos": argumentos
            }

        # ------------------------------------------
        # AÇÕES CRÍTICAS
        # ------------------------------------------

        elif nome_tool in [
            "criar_agendamento",
            "cancelar_agendamento",
            "remarcar_agendamento"
        ]:

            # Nunca executamos automaticamente
            # uma ação crítica.
            return {
                "executado": False,
                "tool": nome_tool,
                "requer_confirmacao": True,
                "argumentos": argumentos
            }

        # ------------------------------------------
        # TOOL DESCONHECIDA
        # ------------------------------------------

        else:

            return {
                "executado": False,
                "erro": "Tool desconhecida."
            }

        return {
            "executado": True,
            "tool": nome_tool,
            "resultado": resultado
        }

    except Exception as erro:

        return {
            "executado": False,
            "tool": nome_tool,
            "erro": str(erro)
        }


# ==================================================
# TESTE MANUAL DO AGENTE
# ==================================================

if __name__ == "__main__":

    mensagem = input(
        "Você: "
    ).strip()

    if not mensagem:

        print(
            "Digite uma mensagem."
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

        decisao = decidir_tool(
            interpretacao,
            mensagem
        )

        print(
            "\n=== DECISÃO DO AGENTE ==="
        )

        print(
            json.dumps(
                decisao,
                indent=4,
                ensure_ascii=False
            )
        )

        resultado = executar_tool(
            decisao
        )

        print(
            "\n=== RESULTADO ==="
        )

        print(
            json.dumps(
                resultado,
                indent=4,
                ensure_ascii=False,
                default=str
            )
        )