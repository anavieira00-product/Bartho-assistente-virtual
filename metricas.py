import json
import time
import uuid
from datetime import datetime
from pathlib import Path


ARQUIVO_METRICAS = Path(__file__).with_name("bartho_metricas.jsonl")

_sessao = None


def _agora():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def iniciar_sessao():
    global _sessao

    _sessao = {
        "session_id": uuid.uuid4().hex[:12],
        "inicio": _agora(),
        "inicio_perf": time.perf_counter(),
        "fim": None,

        # Cada problema/demanda resolvida fica registrado aqui.
        "resolucoes": [],

        # Atendimento humano
        "handoff": False,
        "motivo_handoff": None,

        # Uso do LLM
        "qwen_calls": 0,
        "qwen_tempo_s": 0.0,

        # Recursos utilizados
        "rag_usado": False,
        "tools": [],

        # Controle da sessão
        "interacoes": 0,
        "erros": 0,
    }

    return _sessao["session_id"]


def sessao_atual():
    global _sessao

    if _sessao is None:
        iniciar_sessao()

    return _sessao


def registrar_interacao():
    sessao_atual()["interacoes"] += 1


def registrar_qwen(duracao_s):
    sessao = sessao_atual()

    sessao["qwen_calls"] += 1
    sessao["qwen_tempo_s"] += float(duracao_s or 0)


def registrar_rag():
    sessao_atual()["rag_usado"] = True


def registrar_tool(nome):
    if not nome:
        return

    sessao = sessao_atual()

    if nome not in sessao["tools"]:
        sessao["tools"].append(nome)


def registrar_resolucao(
    categoria,
    orientacao=False,
    motivo=None
):
    sessao = sessao_atual()

    resultado = (
        "resolvido_com_orientacao"
        if orientacao
        else "resolvido_completo"
    )

    resolucao = {
        "timestamp": _agora(),
        "resultado": resultado,
        "categoria": categoria,
        "motivo": motivo,
    }

    sessao["resolucoes"].append(resolucao)


def registrar_handoff(
    motivo="solicitado_pelo_usuario"
):
    sessao = sessao_atual()

    sessao["handoff"] = True
    sessao["motivo_handoff"] = motivo


def registrar_erro():
    sessao_atual()["erros"] += 1


def finalizar_sessao(abandono=False):
    global _sessao

    if _sessao is None:
        return None

    sessao = _sessao

    sessao["fim"] = _agora()

    sessao["tempo_total_s"] = round(
        time.perf_counter() - sessao["inicio_perf"],
        3
    )

    sessao["qwen_tempo_s"] = round(
        sessao["qwen_tempo_s"],
        3
    )

    resolucoes = sessao["resolucoes"]

    completos = sum(
        1
        for item in resolucoes
        if item["resultado"] == "resolvido_completo"
    )

    orientacoes = sum(
        1
        for item in resolucoes
        if item["resultado"] == "resolvido_com_orientacao"
    )

    total_resolucoes = completos + orientacoes

    if sessao["handoff"]:
        resultado_sessao = "handoff_humano"

    elif total_resolucoes > 0:
        resultado_sessao = "resolvido_sem_humano"

    elif abandono:
        resultado_sessao = "abandono"

    else:
        resultado_sessao = "nao_classificado"

    registro = {
        "session_id": sessao["session_id"],
        "inicio": sessao["inicio"],
        "fim": sessao["fim"],

        "resultado_sessao": resultado_sessao,

        "resolucoes": resolucoes,
        "total_resolucoes": total_resolucoes,
        "resolucoes_completas": completos,
        "resolucoes_com_orientacao": orientacoes,

        "handoff": sessao["handoff"],
        "motivo_handoff": sessao["motivo_handoff"],

        "qwen_calls": sessao["qwen_calls"],
        "qwen_tempo_s": sessao["qwen_tempo_s"],

        "rag_usado": sessao["rag_usado"],
        "tools": sessao["tools"],

        "interacoes": sessao["interacoes"],
        "erros": sessao["erros"],

        "tempo_total_s": sessao["tempo_total_s"],
    }

    try:
        with ARQUIVO_METRICAS.open(
            "a",
            encoding="utf-8"
        ) as arquivo:

            arquivo.write(
                json.dumps(
                    registro,
                    ensure_ascii=False
                )
                + "\n"
            )

    finally:
        _sessao = None

    return registro