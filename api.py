import os
import re
import uuid
import unicodedata
from threading import RLock
from contextlib import redirect_stdout
from io import StringIO

from flask import Flask, request, jsonify
from flask_cors import CORS

os.environ["BARTHO_MODO_WEB"] = "1"

from bartho import processar_mensagem, PerguntaInterativaWeb
from agenda import (
    criar_banco, cadastrar_medicos, cadastrar_dias_atendimento,
    consultar_especialidades, consultar_medicos_por_especialidade,
    consultar_dias_medico, verificar_atendimento, consultar_horarios_livres,
    criar_agendamento,
)
from calendario import resolver_data, consultorio_aberto, horario_permitido

app = Flask(__name__)
app.secret_key = os.environ.get("BARTHO_SECRET_KEY") or os.urandom(32).hex()
origem_permitida = os.environ.get("BARTHO_ALLOWED_ORIGIN", "https://anavieira00-product.github.io")
CORS(app, resources={r"/*": {"origins": origem_permitida}})

# Estado separado por conversa. O frontend envia um identificador persistido no navegador.
# Nota: memória temporária do processo; será perdida se o serviço reiniciar.
_CONVERSAS = {}
_LOCK = RLock()

ESPECIALIDADES = {
    "cardiologia": ("cardiologia", "cardiologista", "coração", "coracao", "médico do coração", "medico do coracao"),
    "dermatologia": ("dermatologia", "dermatologista"),
    "clinica_geral": ("clínica geral", "clinica geral", "clínico geral", "clinico geral"),
}

def normalizar(texto):
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9:/\s-]", " ", texto).split())

def extrair_especialidade(texto):
    t = normalizar(texto)
    for canonica, aliases in ESPECIALIDADES.items():
        if any(normalizar(a) in t for a in aliases):
            return canonica
    return None

def sim(texto):
    return normalizar(texto) in {"sim", "s", "claro", "quero", "pode", "pode ser", "isso", "confirmo", "ok", "okay"}

def nao(texto):
    return normalizar(texto) in {"nao", "n", "nao quero", "agora nao", "depois"}

def faq(texto):
    t = normalizar(texto)
    if any(x in t for x in ("endereco", "onde fica", "localizacao", "local da clinica", "como chegar")):
        return "O endereço cadastrado da Clínica Bem é Avenida x, 1, Belo Horizonte/MG."
    if any(x in t for x in ("quanto custa", "qual o valor", "valor da consulta", "preco da consulta", "preço da consulta")):
        return "A consulta particular custa R$ 70,00."
    if any(x in t for x in ("unimed", "bradesco saude", "sulamerica", "convenio", "convênio", "planos aceitos")):
        return "A Clínica Bem aceita Unimed, Bradesco Saúde e SulAmérica. A cobertura depende das condições do plano contratado."
    if any(x in t for x in ("quais especialidades", "especialidades atende", "especialidades tem", "que especialidades")):
        return "A Clínica Bem atende Cardiologia, Dermatologia e Clínica Geral."
    if any(x in t for x in ("horario de funcionamento", "que horas abre", "que horas fecha", "funcionamento da clinica", "horario da clinica")):
        return "A Clínica Bem funciona de segunda a sexta-feira, das 08:00 às 18:00. O último horário de consulta começa às 17:00."
    if any(x in t for x in ("documentos", "o que levar", "preciso levar", "documento para consulta")):
        return "Leve um documento oficial com foto. Se utilizar convênio, leve também a carteirinha, quando aplicável. Menores de idade devem estar acompanhados por responsável."
    if any(x in t for x in ("atraso", "chegar atrasado", "tolerancia")):
        return "A tolerância para atrasos é de até 15 minutos."
    if any(x in t for x in ("cirurgia", "exame de sangue", "exames", "emergencia", "emergência")):
        if "emergencia" in t:
            return "A Clínica Bem não presta atendimento de emergência. Em caso de urgência, procure um serviço de urgência/emergência."
        return "A Clínica Bem realiza consultas médicas nas especialidades cadastradas, mas não realiza cirurgias nem exames de sangue."
    return None

def responder(texto):
    return jsonify({"resposta": texto, "aguardando_resposta": False})

def iniciar_por_especialidade(sessao, especialidade):
    """Inicia a jornada a partir de uma especialidade reconhecida."""
    medicos = consultar_medicos_por_especialidade(especialidade)
    if not medicos:
        sessao.clear()
        sessao["etapa"] = "especialidade"
        return responder("Não encontrei médico cadastrado para essa especialidade. Você pode escolher Cardiologia, Dermatologia ou Clínica Geral.")

    medico = medicos[0]
    dias = consultar_dias_medico(medico)
    nomes_dias = [
        "segunda-feira", "terça-feira", "quarta-feira",
        "quinta-feira", "sexta-feira", "sábado", "domingo"
    ]
    dias_texto = ", ".join(
        nomes_dias[d] for d in dias if isinstance(d, int) and 0 <= d < 7
    )
    nome = {
        "cardiologia": "Cardiologia",
        "dermatologia": "Dermatologia",
        "clinica_geral": "Clínica Geral",
    }[especialidade]
    sessao.clear()
    sessao.update({
        "etapa": "confirmar_horarios",
        "especialidade": especialidade,
        "medico": medico,
        "dias_atendimento": dias,
    })
    complemento = f" Os dias de atendimento são: {dias_texto}." if dias_texto else ""
    return responder(
        f"Temos atendimento de {nome} com {medico}.{complemento} "
        "Quer consultar os horários disponíveis?"
    )


def iniciar_agendamento(sessao, texto=""):
    """Inicia o agendamento aproveitando os dados já presentes na mensagem."""
    especialidade = extrair_especialidade(texto)
    if especialidade:
        return iniciar_por_especialidade(sessao, especialidade)

    sessao.clear()
    sessao["etapa"] = "especialidade"
    return responder(
        "Claro! Qual especialidade você procura: Cardiologia, Dermatologia ou Clínica Geral?"
    )

def processar_fluxo(texto, sessao):
    etapa = sessao.get("etapa")
    t = normalizar(texto)

    # Permite perguntas informativas durante uma jornada sem perder a etapa.
    resposta_faq = faq(texto)
    if resposta_faq:
        return responder(resposta_faq)

    if etapa == "especialidade":
        esp = extrair_especialidade(texto)
        if not esp:
            return responder("Não identifiquei a especialidade. Você procura Cardiologia, Dermatologia ou Clínica Geral?")
        return iniciar_por_especialidade(sessao, esp)

    if etapa == "confirmar_horarios":
        if nao(texto):
            sessao.clear()
            return responder("Tudo bem! Quando quiser, posso ajudar a consultar os horários ou agendar uma consulta.")
        if not sim(texto):
            return responder("Você gostaria de consultar os horários disponíveis? Responda sim ou não.")
        sessao["etapa"] = "data"
        dias = sessao.get("dias_atendimento") or consultar_dias_medico(sessao["medico"])
        nomes_dias = [
            "segunda-feira", "terça-feira", "quarta-feira",
            "quinta-feira", "sexta-feira", "sábado", "domingo"
        ]
        dias_texto = ", ".join(
            nomes_dias[d] for d in dias if isinstance(d, int) and 0 <= d < 7
        )
        if dias_texto:
            return responder(
                f"Qual data você prefere? {sessao['medico']} atende em {dias_texto}. "
                "Você pode informar uma data ou um desses dias."
            )
        return responder(
            "Para qual dia você gostaria de consultar os horários? "
            "Pode dizer hoje, amanhã ou um dia da semana."
        )

    if etapa == "data":
        data = resolver_data(texto)
        if data is None:
            return responder("Não consegui identificar a data. Informe hoje, amanhã ou um dia da semana, como terça-feira.")
        if not consultorio_aberto(data):
            return responder("A clínica funciona de segunda a sexta-feira. Escolha um dia útil, por favor.")
        medico = sessao["medico"]
        dias = consultar_dias_medico(medico)
        if data.weekday() not in dias:
            dias_nomes = ["segunda-feira","terça-feira","quarta-feira","quinta-feira","sexta-feira","sábado","domingo"]
            disponiveis = ", ".join(dias_nomes[d] for d in dias)
            return responder(f"{medico} não atende nesse dia. Os dias de atendimento são: {disponiveis}. Qual desses dias prefere?")
        sessao["data"] = data.strftime("%Y-%m-%d")
        livres = consultar_horarios_livres(medico, sessao["data"])
        livres = [h for h in livres if horario_permitido(h)]
        if not livres:
            return responder("Não encontrei horários livres nessa data. Informe outro dia, por favor.")
        sessao["horarios_livres"] = livres
        sessao["etapa"] = "horario"
        return responder("Horários disponíveis: " + ", ".join(livres) + ". Qual horário você prefere?")

    if etapa == "horario":
        horario = t.replace("h", ":00") if re.fullmatch(r"\d{1,2}h", t) else t
        if re.fullmatch(r"\d{1,2}", horario):
            horario = horario.zfill(2) + ":00"
        if horario not in sessao.get("horarios_livres", []):
            return responder("Esse horário não está na lista de disponíveis. Escolha um destes: " + ", ".join(sessao.get("horarios_livres", [])) + ".")
        sessao["horario"] = horario
        sessao["etapa"] = "paciente"
        return responder("Perfeito. Qual é o nome completo do paciente?")

    if etapa == "paciente":
        nome = texto.strip()
        if len(nome) < 2 or nome.isdigit():
            return responder("Pode informar o nome do paciente, por favor?")
        sessao["paciente"] = nome
        data_br = sessao["data"]
        from datetime import datetime
        data_br = datetime.strptime(data_br, "%Y-%m-%d").strftime("%d/%m/%Y")
        sessao["etapa"] = "confirmacao"
        return responder(f"Confirme os dados: {sessao['especialidade'].replace('_',' ').title()}, {data_br} às {sessao['horario']}, paciente {nome}. Deseja confirmar?")

    if etapa == "confirmacao":
        if nao(texto):
            sessao.clear()
            return responder("Tudo bem, não fiz o agendamento. Se quiser, podemos começar novamente.")
        if not sim(texto):
            return responder("Para concluir, responda sim para confirmar ou não para cancelar.")
        sucesso = criar_agendamento(sessao["paciente"], sessao["medico"], sessao["data"], sessao["horario"])
        if sucesso:
            from datetime import datetime
            data_br = datetime.strptime(sessao["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
            resultado = f"Consulta agendada com sucesso! {data_br} às {sessao['horario']}, com {sessao['medico']}."
        else:
            resultado = "Esse horário acabou de ficar indisponível. Vamos escolher outro horário."
        sessao.clear()
        return responder(resultado)

    return None

@app.get("/health")
def health():
    return jsonify({"assistente": "Bartho", "status": "ok"})

@app.post("/mensagem")
def mensagem():
    dados = request.get_json(silent=True) or {}
    texto = str(dados.get("mensagem", "")).strip()
    conversa_id = str(dados.get("conversa_id", "")).strip()
    if not texto:
        return jsonify({"erro": "Mensagem não informada."}), 400
    if not conversa_id:
        return jsonify({"erro": "Identificador da conversa ausente. Atualize a página e tente novamente."}), 400

    with _LOCK:
        sessao = _CONVERSAS.setdefault(conversa_id, {})
        texto_normalizado = normalizar(texto)

        if texto_normalizado in {"oi", "ola", "opa", "bom dia", "boa tarde", "boa noite", "oi tudo bem", "ola tudo bem"} and not sessao.get("etapa"):
            return responder("Olá! 👋 Sou o Bartho, assistente virtual da Clínica Bem. Como posso ajudar?")

        # Um pedido explícito novo inicia fluxo novo.
        if any(x in texto_normalizado for x in (
            "quero agendar", "agendar consulta", "marcar consulta", "quero marcar",
            "gostaria de agendar", "gostaria de marcar", "preciso agendar", "preciso marcar"
        )):
            return iniciar_agendamento(sessao, texto)

        if sessao.get("etapa"):
            resultado = processar_fluxo(texto, sessao)
            if resultado is not None:
                return resultado

        # Mantém a escolha de especialidade como continuação contextual:
        # após perguntar quais especialidades existem, uma resposta como
        # "Cardiologia" deve avançar para a jornada, não cair no fluxo legado.
        pergunta_especialidades = any(
            termo in texto_normalizado
            for termo in (
                "quais especialidades",
                "especialidades atende",
                "especialidades tem",
                "que especialidades",
            )
        )
        resposta_faq = faq(texto)
        if resposta_faq:
            if pergunta_especialidades:
                sessao["etapa"] = "especialidade"
            return responder(resposta_faq)

        # Recuperação: se a sessão se perdeu após reinicialização/deploy,
        # uma especialidade isolada ainda inicia o fluxo de agendamento.
        especialidade_isolada = extrair_especialidade(texto)
        if especialidade_isolada:
            return iniciar_por_especialidade(sessao, especialidade_isolada)

    captura = StringIO()
    try:
        with redirect_stdout(captura):
            resultado = processar_mensagem(texto)
        saida = captura.getvalue().strip()
        resposta = ""
        for linha in reversed(saida.splitlines()):
            linha = linha.strip()
            if linha.startswith("Bartho:"):
                resposta = linha.split("Bartho:", 1)[1].strip()
                break
        if not resposta and isinstance(resultado, str):
            resposta = resultado.strip()
        if not resposta:
            resposta = "Como posso ajudar?"
        return jsonify({"resposta": resposta, "resultado": resultado})
    except PerguntaInterativaWeb as pergunta:
        # The legacy terminal flow cannot resume its Python call stack across HTTP requests.
        # Avoid pretending the interactive flow is resumable; return a safe explanation.
        app.logger.warning("Fluxo legado solicitou entrada web sem estado de continuação: %s", pergunta.pergunta)
        return jsonify({
            "resposta": "Posso ajudar com informações da clínica e iniciar um agendamento. Para continuar essa solicitação, vamos começar pelo botão de agendamento ou escreva 'Quero agendar uma consulta'.",
            "aguardando_resposta": False
        })
    except Exception:
        app.logger.exception("Erro ao processar mensagem do Bartho")
        return jsonify({"erro": "Ocorreu uma falha ao processar sua mensagem. Tente novamente."}), 500

# Inicializa tabelas e cadastros necessários para o MVP.
try:
    criar_banco()
    cadastrar_medicos()
    cadastrar_dias_atendimento()
except Exception:
    app.logger.exception("Não foi possível inicializar a agenda do Bartho")

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=porta, debug=False)
