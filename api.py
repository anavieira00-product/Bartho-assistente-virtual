import os
import re
import uuid
import unicodedata
import json
import sqlite3
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher
from pathlib import Path
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
    criar_agendamento, buscar_agendamentos, cancelar_agendamento, remarcar_agendamento,
)
from calendario import resolver_data, consultorio_aberto, horario_permitido

app = Flask(__name__)
# Identificador exclusivo desta versão para confirmar o deploy no Render.
API_VERSION = "2026-10-14-conversa-contexto-v6"
app.secret_key = os.environ.get("BARTHO_SECRET_KEY") or os.urandom(32).hex()
origem_permitida = os.environ.get("BARTHO_ALLOWED_ORIGIN", "https://anavieira00-product.github.io")
CORS(app, resources={r"/*": {"origins": origem_permitida}})

# Estado separado por conversa e persistido em SQLite, em vez de ficar apenas na RAM.
# Em produção, configure BARTHO_SESSION_DB_PATH para um disco persistente do Render
# (por exemplo, /var/data/bartho_sessions.db).
_SESSION_DB = os.environ.get("BARTHO_SESSION_DB_PATH", "bartho_sessions.db")
_LOCK = RLock()
_FUSO_LOCAL = ZoneInfo("America/Sao_Paulo")

def agora_local():
    """Retorna a hora local de Belo Horizonte, independentemente do fuso do servidor."""
    return datetime.now(_FUSO_LOCAL).replace(tzinfo=None)


def _conectar_sessoes():
    caminho = Path(_SESSION_DB)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(caminho), timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS conversas_web (id TEXT PRIMARY KEY, estado TEXT NOT NULL, atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    con.commit()
    return con


def _salvar_sessao(conversa_id, estado):
    with _conectar_sessoes() as con:
        con.execute(
            "INSERT INTO conversas_web (id, estado, atualizado_em) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(id) DO UPDATE SET estado=excluded.estado, atualizado_em=CURRENT_TIMESTAMP",
            (conversa_id, json.dumps(dict(estado), ensure_ascii=False)),
        )


def _carregar_sessao(conversa_id):
    with _conectar_sessoes() as con:
        linha = con.execute("SELECT estado FROM conversas_web WHERE id = ?", (conversa_id,)).fetchone()
    if not linha:
        return SessaoPersistente(conversa_id, {})
    try:
        estado = json.loads(linha[0])
        if not isinstance(estado, dict):
            estado = {}
    except (TypeError, json.JSONDecodeError):
        estado = {}
    return SessaoPersistente(conversa_id, estado)


class SessaoPersistente(dict):
    """Dicionário compatível com o fluxo atual que salva cada alteração no SQLite."""
    def __init__(self, conversa_id, estado=None):
        self.conversa_id = conversa_id
        super().__init__(estado or {})

    def _persistir(self):
        _salvar_sessao(self.conversa_id, self)

    def __setitem__(self, chave, valor):
        super().__setitem__(chave, valor)
        self._persistir()

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self._persistir()

    def clear(self):
        super().clear()
        self._persistir()

    def pop(self, chave, *args):
        resultado = super().pop(chave, *args)
        self._persistir()
        return resultado

ESPECIALIDADES = {
    "cardiologia": ("cardiologia", "cardiologista", "coração", "coracao", "médico do coração", "medico do coracao"),
    "dermatologia": ("dermatologia", "dermatologista"),
    "clinica_geral": ("clínica geral", "clinica geral", "clínico geral", "clinico geral", "clínico gera", "clinico gera", "clinica gera", "clínico geral.", "medicina geral"),
}

def normalizar(texto):
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9:/\s-]", " ", texto).split())

def extrair_especialidade(texto):
    t = normalizar(texto)
    # Correspondência direta para os nomes e sinônimos mais comuns.
    for canonica, aliases in ESPECIALIDADES.items():
        if any(re.search(r"\b" + re.escape(normalizar(a)) + r"\b", t) for a in aliases):
            return canonica
    # Tolerância a pequenos erros de digitação em respostas curtas, como "clinico gera".
    if len(t.split()) <= 4:
        candidatos = {
            "clinica_geral": ["clinico geral", "clinica geral", "clinico gera", "clinica gera"],
            "cardiologia": ["cardiologia", "cardiologista"],
            "dermatologia": ["dermatologia", "dermatologista"],
        }
        for canonica, aliases in candidatos.items():
            if any(SequenceMatcher(None, t, alias).ratio() >= 0.82 for alias in aliases):
                return canonica
    return None

def sim(texto):
    return normalizar(texto) in {"sim", "s", "claro", "quero", "pode", "pode ser", "isso", "confirmo", "ok", "okay"}

def nao(texto):
    return normalizar(texto) in {"nao", "n", "nao quero", "agora nao", "depois"}

DIAS_SEMANA = {
    "segunda-feira": 0, "segunda": 0,
    "terca-feira": 1, "terca": 1,
    "quarta-feira": 2, "quarta": 2,
    "quinta-feira": 3, "quinta": 3,
    "sexta-feira": 4, "sexta": 4,
    "sabado": 5, "domingo": 6,
}

def identificar_dia_semana(texto):
    """Encontra um dia da semana dentro de frases naturais, como 'e quarta?' ."""
    t = normalizar(texto)
    # Verifica os nomes longos primeiro para evitar correspondências parciais.
    for nome in sorted(DIAS_SEMANA, key=len, reverse=True):
        if re.search(r"\b" + re.escape(nome) + r"\b", t):
            return nome, DIAS_SEMANA[nome]
    return None, None

def pergunta_especialidades(texto):
    """Reconhece perguntas naturais e variações simples sobre especialidades."""
    t = normalizar(texto)
    menciona = any(p in t for p in ("especialidade", "especialidades", "especialida", "especialid"))
    pergunta = any(x in t for x in (
        "qual", "quais", "que", "tem", "atende", "atendem", "oferece", "oferecem",
        "voces tem", "voces atendem", "a clinica tem", "a clinica atende", "trabalha com"
    ))
    return menciona and pergunta or t in {"especialidades", "quais especialidades", "qual especialidade"}

def pergunta_horarios(texto):
    t = normalizar(texto)
    return any(x in t for x in (
        "quais horarios", "qual horario", "horarios disponiveis", "horario disponivel",
        "consultar horarios", "ver horarios", "tem horario", "tem horarios", "quero horario",
        "quero horarios", "horarios voces tem", "quais sao os horarios", "quais os horarios"
    ))

def identificar_convenio(texto):
    """Identifica o convênio mencionado sem depender de uma frase exata."""
    t = normalizar(texto)
    if "unimed" in t:
        return "Unimed"
    if "bradesco" in t:
        return "Bradesco Saúde"
    if "sulamerica" in t or "sul america" in t or "sul-america" in t:
        return "SulAmérica"
    return None

def resolver_data_flexivel(texto):
    """Resolve hoje/amanhã, dias da semana e datas como 16/10 ou 16/10/2026."""
    t = normalizar(texto)
    # Extrai data explícita mesmo quando vem dentro de uma frase ("quero dia 16/10/2026").
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:
        dia, mes, ano = int(m.group(1)), int(m.group(2)), m.group(3)
        ano = int(ano) if ano else agora_local().year
        if ano < 100:
            ano += 2000
        try:
            data = date(ano, mes, dia)
            hoje = agora_local().date()
            if not m.group(3) and data < hoje:
                data = date(ano + 1, mes, dia)
            return datetime.combine(data, datetime.min.time())
        except ValueError:
            return None
    if "hoje" in t:
        return agora_local()
    if "amanha" in t:
        return agora_local() + timedelta(days=1)
    dia_nome, _ = identificar_dia_semana(texto)
    if dia_nome:
        try:
            return resolver_data(dia_nome)
        except Exception:
            return None
    try:
        # Usa o relógio local do Brasil, não o fuso UTC padrão do Render.
        try:
            return resolver_data(texto, data_atual=agora_local())
        except TypeError:
            return resolver_data(texto)
    except Exception:
        return None


def pedido_cancelamento(texto):
    t = normalizar(texto)
    if any(x in t for x in ("nao cancelar", "nao quero cancelar", "nao cancele")):
        return False
    return t in {"cancela", "cancelar", "cancela isso", "cancelar agendamento", "cancela consulta", "quero cancelar", "quero cancelar consulta", "cancelar minha consulta", "cancelar meu agendamento", "desisto", "desistir"} or any(
        x in t for x in ("quero cancelar a consulta", "quero cancelar meu", "cancelar a consulta", "cancelar meu agendamento")
    )

def pedido_remarcacao(texto):
    t = normalizar(texto)
    return any(x in t for x in ("remarcar", "remarcacao", "reagendar", "reagendamento", "mudar minha consulta", "alterar minha consulta", "trocar a data da consulta"))

def mensagem_de_agradecimento(texto):
    t = normalizar(texto)
    return t in {"obrigado", "obrigada", "muito obrigado", "muito obrigada", "valeu", "ok obrigada", "ok obrigado", "ta bom obrigada", "ta bom obrigado", "ok valeu"} or t.startswith(("obrigada ", "obrigado ", "muito obrigada", "muito obrigado"))

def mensagem_encerramento(texto):
    t = normalizar(texto)
    return t in {"somente isso", "so isso", "so isso mesmo", "era so isso", "e so isso", "nao obrigada", "nao obrigado", "nao, obrigada", "nao, obrigado", "tchau", "ate logo", "ate mais", "pode encerrar", "pode finalizar"}

def eh_pergunta_sobre_consulta_curta(texto):
    t = normalizar(texto)
    return t in {"e consulta", "e a consulta", "consulta", "e para consulta", "e consulta medica", "e a consulta medica", "consulta medica"}


def pedido_nova_data(texto):
    t = normalizar(texto)
    tem_data = bool(re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", t))
    dia, _ = identificar_dia_semana(texto)
    return tem_data or dia is not None or "hoje" in t or "amanha" in t


def pedido_novo_horario(texto):
    t = normalizar(texto)
    return bool(re.search(r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d|h(?:[0-5]\d)?)?\b", t)) and any(x in t for x in (" as ", " horario", " as ", " para ", " as "))


def extrair_horario(texto):
    """Extrai hora sem confundir os números de uma data (16/10/2026) com 16:00."""
    t = normalizar(texto)
    sem_datas = re.sub(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", " ", t)
    m = re.search(r"\b([01]?\d|2[0-3])(?::([0-5]\d)|h(?:([0-5]\d))?)\b", sem_datas)
    if not m:
        m = re.search(r"\bas\s+([01]?\d|2[0-3])\b", sem_datas)
        if not m:
            return None
        hora, minuto = int(m.group(1)), 0
    else:
        hora, minuto = int(m.group(1)), int(m.group(2) or m.group(3) or 0)
    return f"{hora:02d}:{minuto:02d}"

def faq(texto, convenio=None):
    t = normalizar(texto)

    # Follow-up curto após falar de exames/convênio: "e consulta?".
    if eh_pergunta_sobre_consulta_curta(texto):
        plano = convenio or identificar_convenio(texto)
        if plano:
            return f"Sim, a Clínica Bem atende consultas por {plano}. A clínica não realiza exames de sangue nem cirurgias."
        return "Sim, a Clínica Bem atende consultas por Unimed, Bradesco Saúde e SulAmérica. A cobertura depende das condições do convênio informado. A clínica não realiza exames de sangue nem cirurgias."

    # Intenção específica sobre exames/cirurgias tem prioridade sobre a palavra "cobre".
    # Assim, "cobre exame de sangue?" nunca recebe uma resposta afirmativa sobre consultas.
    pergunta_exame = any(x in t for x in (
        "exame de sangue", "exames de sangue", "fazer exame", "faz exame",
        "realiza exame", "realiza exames", "coleta de sangue", "exame laboratorial",
        "exames laboratoriais", "cirurgia", "cirurgias"
    ))
    if pergunta_exame:
        return ("Não. A Clínica Bem não realiza exames de sangue nem cirurgias. "
                "A clínica atende consultas médicas nas especialidades Cardiologia, Dermatologia e Clínica Geral.")

    # Responde sobre cobertura de consulta com a regra de negócio da Clínica Bem:
    # consultas são atendidas pelos convênios aceitos; exames e cirurgias não são realizados.
    pergunta_cobertura = any(x in t for x in (
        "cobre consulta", "cobre consultas", "cobre a consulta", "cobertura da consulta",
        "meu plano cobre", "plano cobre consulta", "consulta e coberta", "consulta cobre",
        "atende consulta pelo convenio", "posso consultar pelo convenio", "tem cobertura",
        "e coberto", "e coberta", "cobre"
    )) and any(x in t for x in ("consulta", "plano", "convenio", "cobre", "cobertura", "coberto", "coberta"))
    if pergunta_cobertura:
        plano = convenio or identificar_convenio(texto)
        if plano:
            return (f"Sim, a Clínica Bem atende consultas por {plano}. A clínica não realiza exames de sangue "
                    "nem cirurgias.")
        return ("Sim, a Clínica Bem atende consultas por Unimed, Bradesco Saúde e SulAmérica. "
                "A clínica não realiza exames de sangue nem cirurgias.")

    # Informar o convênio não deve gerar uma repetição da FAQ de planos aceitos.
    convenio_mencionado = identificar_convenio(texto)
    if convenio_mencionado and any(x in t for x in ("tenho", "uso", "meu convenio", "meu plano", "sou cliente", "sou da")):
        return (f"Entendi, você tem {convenio_mencionado}. A Clínica Bem atende consultas por esse convênio, "
                "conforme as condições do plano. Quer saber sobre uma consulta ou agendar um horário?")

    if any(x in t for x in ("endereco", "onde fica", "localizacao", "local da clinica", "como chegar")):
        return "O endereço cadastrado da Clínica Bem é Avenida x, 1, Belo Horizonte/MG."
    if any(x in t for x in ("quanto custa", "qual o valor", "valor da consulta", "preco da consulta")):
        return "A consulta particular custa R$ 70,00."
    if any(x in t for x in ("convenio", "planos aceitos", "quais planos", "quais convenios", "aceita plano", "aceita convenio")):
        return "A Clínica Bem aceita Unimed, Bradesco Saúde e SulAmérica para consultas, conforme as condições de cada plano. A clínica não realiza exames de sangue nem cirurgias."
    if pergunta_especialidades(texto):
        return "A Clínica Bem atende Cardiologia, Dermatologia e Clínica Geral."
    if any(x in t for x in ("horario de funcionamento", "que horas abre", "que horas fecha", "funcionamento da clinica", "horario da clinica", "que horas funciona")):
        return "A Clínica Bem funciona de segunda a sexta-feira, das 08:00 às 18:00. O último horário para iniciar uma consulta é às 17:00."
    if any(x in t for x in ("documentos", "o que levar", "preciso levar", "documento para consulta")):
        return "Leve um documento oficial com foto. Se utilizar convênio, leve também a carteirinha, quando aplicável. Menores de idade devem estar acompanhados por responsável."
    if any(x in t for x in ("atraso", "chegar atrasado", "tolerancia")):
        return "A tolerância para atrasos é de até 15 minutos."
    if "emergencia" in t:
        return "A Clínica Bem não presta atendimento de emergência. Em caso de urgência, procure um serviço de urgência/emergência."
    if any(x in t for x in ("cirurgia", "exame de sangue", "exames")):
        return "A Clínica Bem realiza consultas médicas nas especialidades cadastradas, mas não realiza cirurgias nem exames de sangue."
    return None

def responder(texto):
    return jsonify({"resposta": texto, "aguardando_resposta": False})

def iniciar_agendamento(sessao, texto=""):
    """Inicia o agendamento aproveitando os dados já presentes na mensagem."""
    sessao.clear()
    especialidade = extrair_especialidade(texto)

    if especialidade:
        medicos = consultar_medicos_por_especialidade(especialidade)
        if medicos:
            medico = medicos[0]
            dias = consultar_dias_medico(medico)
            nomes_dias = [
                "segunda-feira", "terça-feira", "quarta-feira",
                "quinta-feira", "sexta-feira", "sábado", "domingo"
            ]
            dias_texto = ", ".join(
                nomes_dias[d] for d in dias if isinstance(d, int) and 0 <= d < 7
            )
            sessao.update({
                "etapa": "confirmar_horarios",
                "especialidade": especialidade,
                "medico": medico,
                "dias_atendimento": dias,
            })
            nome = {
                "cardiologia": "Cardiologia",
                "dermatologia": "Dermatologia",
                "clinica_geral": "Clínica Geral",
            }[especialidade]
            if dias_texto:
                return responder(
                    f"Temos atendimento de {nome} com {medico}. "
                    f"Os dias de atendimento são: {dias_texto}. "
                    "Quer consultar os horários disponíveis?"
                )
            return responder(
                f"Temos atendimento de {nome} com {medico}. "
                "Quer consultar os horários disponíveis?"
            )

    sessao["etapa"] = "especialidade"
    return responder(
        "Claro! Qual especialidade você procura: Cardiologia, Dermatologia ou Clínica Geral?"
    )

def _guardar_agendamento_escolhido(sessao, agendamento):
    # agenda.buscar_agendamentos retorna (id, medico, especialidade, data, horario)
    sessao["agendamento_id"] = agendamento[0]
    sessao["medico"] = agendamento[1]
    sessao["especialidade"] = agendamento[2]
    sessao["data_anterior"] = agendamento[3]
    sessao["horario_anterior"] = agendamento[4]


def _iniciar_acao_agendamento(sessao, acao):
    sessao.clear()
    sessao["acao_agendamento"] = acao
    sessao["etapa"] = "cancelamento_nome" if acao == "cancelar" else "remarcar_nome"
    verbo = "cancelar" if acao == "cancelar" else "remarcar"
    return responder(f"Claro, posso ajudar a {verbo} uma consulta. Qual é o nome completo do paciente usado no agendamento?")


def processar_fluxo(texto, sessao):
    etapa = sessao.get("etapa")
    t = normalizar(texto)

    # Fluxo de cancelamento de uma consulta já marcada.
    if etapa == "cancelamento_nome":
        nome = texto.strip()
        if len(nome) < 2 or nome.isdigit():
            return responder("Informe o nome completo do paciente para localizar o agendamento.")
        consultas = buscar_agendamentos(nome)
        if not consultas:
            return responder("Não encontrei agendamentos com esse nome. Confira se informou o mesmo nome usado na marcação ou diga 'encerrar' para sair.")
        if len(consultas) == 1:
            _guardar_agendamento_escolhido(sessao, consultas[0])
            sessao["etapa"] = "cancelamento_confirmar"
            data_br = str(consultas[0][3])
            try:
                data_br = datetime.strptime(data_br, "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                pass
            return responder(f"Encontrei a consulta de {data_br} às {consultas[0][4]} com {consultas[0][1]}. Confirma o cancelamento? Responda sim ou não.")
        sessao["agendamentos_encontrados"] = [list(a) for a in consultas]
        sessao["etapa"] = "cancelamento_escolher"
        linhas = []
        for i, a in enumerate(consultas, 1):
            try:
                data_br = datetime.strptime(str(a[3]), "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                data_br = str(a[3])
            linhas.append(f"{i}. {data_br} às {a[4]} com {a[1]}")
        return responder("Encontrei mais de uma consulta:\n" + "\n".join(linhas) + "\nQual deseja cancelar? Informe o número.")

    if etapa == "cancelamento_escolher":
        consultas = sessao.get("agendamentos_encontrados", [])
        if not t.isdigit() or not (1 <= int(t) <= len(consultas)):
            return responder("Informe o número da consulta que deseja cancelar.")
        ag = consultas[int(t) - 1]
        _guardar_agendamento_escolhido(sessao, ag)
        sessao["etapa"] = "cancelamento_confirmar"
        try:
            data_br = datetime.strptime(str(ag[3]), "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            data_br = str(ag[3])
        return responder(f"Confirma o cancelamento da consulta de {data_br} às {ag[4]} com {ag[1]}? Responda sim ou não.")

    if etapa == "cancelamento_confirmar":
        if nao(texto):
            sessao.clear()
            return responder("Tudo bem, mantive sua consulta. Posso ajudar com mais alguma coisa?")
        if not sim(texto):
            return responder("Deseja mesmo cancelar essa consulta? Responda sim ou não.")
        sucesso = cancelar_agendamento(sessao.get("agendamento_id"))
        sessao.clear()
        return responder("Pronto, sua consulta foi cancelada." if sucesso else "Não consegui cancelar essa consulta. Ela pode já ter sido alterada. Posso ajudar com mais alguma coisa?")

    # Fluxo de remarcação de uma consulta já marcada.
    if etapa == "remarcar_nome":
        nome = texto.strip()
        if len(nome) < 2 or nome.isdigit():
            return responder("Informe o nome completo do paciente para localizar o agendamento.")
        consultas = buscar_agendamentos(nome)
        if not consultas:
            return responder("Não encontrei agendamentos com esse nome. Confira se informou o mesmo nome usado na marcação ou diga 'encerrar' para sair.")
        if len(consultas) == 1:
            _guardar_agendamento_escolhido(sessao, consultas[0])
            sessao["etapa"] = "remarcar_data"
            return responder("Encontrei sua consulta. Qual nova data você prefere? Pode informar, por exemplo, 20/10/2026 ou sexta-feira.")
        sessao["agendamentos_encontrados"] = [list(a) for a in consultas]
        sessao["etapa"] = "remarcar_escolher"
        linhas = []
        for i, a in enumerate(consultas, 1):
            try:
                data_br = datetime.strptime(str(a[3]), "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                data_br = str(a[3])
            linhas.append(f"{i}. {data_br} às {a[4]} com {a[1]}")
        return responder("Encontrei mais de uma consulta:\n" + "\n".join(linhas) + "\nQual deseja remarcar? Informe o número.")

    if etapa == "remarcar_escolher":
        consultas = sessao.get("agendamentos_encontrados", [])
        if not t.isdigit() or not (1 <= int(t) <= len(consultas)):
            return responder("Informe o número da consulta que deseja remarcar.")
        _guardar_agendamento_escolhido(sessao, consultas[int(t) - 1])
        sessao["etapa"] = "remarcar_data"
        return responder("Qual nova data você prefere? Pode informar, por exemplo, 20/10/2026 ou sexta-feira.")

    if etapa == "remarcar_data":
        nova_data = resolver_data_flexivel(texto)
        if nova_data is None:
            return responder("Não consegui identificar a data. Informe uma data como 20/10/2026, 20/10, amanhã ou um dia da semana.")
        hoje = agora_local().date()
        if nova_data.date() < hoje:
            return responder("Essa data já passou. Informe uma data futura.")
        if not consultorio_aberto(nova_data):
            return responder("A clínica atende de segunda a sexta-feira. Informe uma data em dia útil.")
        dias = consultar_dias_medico(sessao["medico"])
        if nova_data.weekday() not in dias:
            nomes = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
            dias_txt = ", ".join(nomes[d] for d in dias if isinstance(d, int) and 0 <= d < 7)
            return responder(f"{sessao['medico']} não atende nessa data. Os dias de atendimento são: {dias_txt}. Informe outra data.")
        if nova_data.date() == hoje and agora_local().hour >= 17:
            return responder("Hoje não há mais horários para iniciar consultas. Informe outra data, por favor.")
        livres = consultar_horarios_livres(sessao["medico"], nova_data.strftime("%Y-%m-%d"))
        livres = [h for h in livres if horario_permitido(h)]
        if nova_data.date() == hoje:
            agora = agora_local()
            livres = [h for h in livres if (int(h[:2]), int(h[3:])) > (agora.hour, agora.minute)]
        if not livres:
            return responder("Não encontrei horários disponíveis nessa data. Informe outra data.")
        sessao["nova_data"] = nova_data.strftime("%Y-%m-%d")
        sessao["horarios_livres_remarcacao"] = livres
        sessao["etapa"] = "remarcar_horario"
        return responder("Horários disponíveis: " + ", ".join(livres) + ". Qual horário prefere?")

    if etapa == "remarcar_horario":
        if pedido_nova_data(texto):
            sessao["etapa"] = "remarcar_data"
            return processar_fluxo(texto, sessao)
        horario = extrair_horario(texto)
        if not horario and re.fullmatch(r"\d{1,2}", t):
            horario = f"{int(t):02d}:00"
        if horario not in sessao.get("horarios_livres_remarcacao", []):
            return responder("Esse horário não está disponível. Escolha um dos horários listados ou informe outra data.")
        sessao["novo_horario"] = horario
        sessao["etapa"] = "remarcar_confirmar"
        data_br = datetime.strptime(sessao["nova_data"], "%Y-%m-%d").strftime("%d/%m/%Y")
        return responder(f"Confirma a remarcação para {data_br} às {horario}? Responda sim ou não.")

    if etapa == "remarcar_confirmar":
        if nao(texto):
            sessao.clear()
            return responder("Tudo bem, não alterei sua consulta. Posso ajudar com mais alguma coisa?")
        if not sim(texto):
            return responder("Confirma a remarcação? Responda sim ou não.")
        sucesso = remarcar_agendamento(sessao["agendamento_id"], sessao["nova_data"], sessao["novo_horario"])
        sessao.clear()
        return responder("Pronto, sua consulta foi remarcada com sucesso." if sucesso else "Não consegui remarcar para esse horário. Ele pode ter ficado indisponível. Posso consultar outra opção.")

    if etapa in {"data", "horario"} and nao(texto):
        sessao.clear()
        return responder("Tudo bem, encerrei esta consulta de horários. Nenhum agendamento foi confirmado. Se quiser, podemos começar novamente.")

    # Perguntas informativas têm prioridade e não devem cair no fluxo legado.
    resposta_faq = faq(texto, sessao.get("convenio_informado"))
    if resposta_faq:
        if pergunta_especialidades(texto):
            sessao["etapa"] = "especialidade"
        return responder(resposta_faq)

    # A pessoa pode mudar a especialidade ou perguntar pelos horários usando linguagem natural.
    esp = extrair_especialidade(texto)
    if etapa in {"especialidade", "apos_lista_especialidades"}:
        if not esp:
            return responder("Qual especialidade você procura: Cardiologia, Dermatologia ou Clínica Geral?")
        medicos = consultar_medicos_por_especialidade(esp)
        if not medicos:
            return responder("Não encontrei médico cadastrado para essa especialidade. Posso ajudar com outra?")
        medico = medicos[0]
        dias = consultar_dias_medico(medico)
        sessao.update({"especialidade": esp, "medico": medico, "dias_atendimento": dias, "etapa": "confirmar_horarios"})
        nomes = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
        dias_txt = ", ".join(nomes[d] for d in dias if isinstance(d, int) and 0 <= d < 7)
        nome = {"cardiologia":"Cardiologia", "dermatologia":"Dermatologia", "clinica_geral":"Clínica Geral"}[esp]
        return responder(f"Temos atendimento de {nome} com {medico}. Os dias de atendimento são: {dias_txt}. Você pode pedir os horários ou informar diretamente o dia desejado.")

    if etapa == "confirmar_horarios":
        if pergunta_horarios(texto) or sim(texto):
            sessao["etapa"] = "data"
            dias = sessao.get("dias_atendimento") or consultar_dias_medico(sessao["medico"])
            nomes = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
            dias_txt = ", ".join(nomes[d] for d in dias if isinstance(d, int) and 0 <= d < 7)
            return responder(f"Para consultar horários, informe a data desejada. {sessao['medico']} atende em {dias_txt}. Você pode dizer, por exemplo, ‘quinta-feira’ ou ‘16/10/2026’.")
        dia_nome, dia_numero = identificar_dia_semana(texto)
        data_candidata = resolver_data_flexivel(texto) if pedido_nova_data(texto) else None
        # Aceita uma data numérica diretamente depois da escolha da especialidade
        # (ex.: "quero dia 20/10"), sem exigir primeiro um "sim".
        if dia_nome or data_candidata:
            sessao["etapa"] = "data"
            return processar_fluxo(texto, sessao)
        if nao(texto):
            sessao.clear()
            return responder("Tudo bem! Quando quiser, posso ajudar a consultar os horários ou agendar uma consulta.")
        return responder("Você pode informar diretamente o dia ou a data desejada, ou pedir para consultar os horários disponíveis.")

    if etapa == "data":
        # Se a pessoa informar data e horário juntos (ex.: “sexta às 18”), valida ambos.
        hora_pedida_na_mensagem = extrair_horario(texto)
        if hora_pedida_na_mensagem and pedido_nova_data(texto):
            sessao["horario_solicitado"] = hora_pedida_na_mensagem
        data = resolver_data_flexivel(texto)
        if data is None:
            return responder("Não consegui identificar a data. Informe uma data como 16/10/2026, 16/10, hoje, amanhã ou um dia da semana.")
        agora = agora_local()
        data_dia = data.date()
        hoje = agora.date()
        if data_dia < hoje:
            return responder("Essa data já passou. Informe uma data futura, por favor.")
        if not consultorio_aberto(data):
            return responder("A clínica funciona de segunda a sexta-feira. Escolha um dia útil, por favor.")
        medico = sessao.get("medico")
        if not medico:
            sessao["etapa"] = "especialidade"
            return responder("Antes de consultar os horários, qual especialidade você procura: Cardiologia, Dermatologia ou Clínica Geral?")
        dias = sessao.get("dias_atendimento") or consultar_dias_medico(medico)
        if data.weekday() not in dias:
            dias_nomes = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
            disponiveis = ", ".join(dias_nomes[d] for d in dias if isinstance(d, int) and 0 <= d < 7)
            return responder(f"{medico} não atende nessa data. Os dias de atendimento são: {disponiveis}. Informe outra data, por favor.")
        # Último horário de consulta começa às 17h. Não oferecer horários passados em uma data de hoje.
        if data_dia == hoje and agora.hour >= 17:
            return responder("Hoje não há mais horários para iniciar consultas. A clínica funciona até 18:00, e o último horário de consulta começa às 17:00. Informe outra data, por favor.")
        sessao["data"] = data.strftime("%Y-%m-%d")
        livres = consultar_horarios_livres(medico, sessao["data"])
        livres = [h for h in livres if horario_permitido(h)]
        if data_dia == hoje:
            livres = [h for h in livres if int(h.split(":")[0]) > agora.hour or (int(h.split(":")[0]) == agora.hour and int(h.split(":")[1]) > agora.minute)]
        if not livres:
            sessao.pop("horarios_livres", None)
            return responder("Não encontrei horários futuros disponíveis nessa data. Informe outra data, por favor.")
        sessao["horarios_livres"] = livres
        sessao["etapa"] = "horario"
        solicitado = sessao.pop("horario_solicitado", None)
        if solicitado:
            if solicitado in livres:
                sessao["horario"] = solicitado
                sessao["etapa"] = "paciente"
                return responder(f"O horário das {solicitado} está disponível. Qual é o nome completo do paciente?")
            return responder(f"Às {solicitado}, não há horário disponível nessa data. Os horários disponíveis são: {', '.join(livres)}. Qual horário você prefere?")
        return responder("Horários disponíveis: " + ", ".join(livres) + ". Qual horário você prefere?")

    if etapa == "horario":
        # Permite trocar a data mesmo quando o bot estava aguardando a escolha de horário.
        if pedido_nova_data(texto):
            hora_pedida = extrair_horario(texto)
            if hora_pedida:
                sessao["horario_solicitado"] = hora_pedida
            sessao.pop("data", None)
            sessao.pop("horario", None)
            sessao.pop("horarios_livres", None)
            sessao["etapa"] = "data"
            return processar_fluxo(texto, sessao)
        horario = extrair_horario(texto)
        if not horario and re.fullmatch(r"\d{1,2}", t):
            horario = f"{int(t):02d}:00"
        if not horario:
            return responder("Informe um horário da lista ou uma nova data para consultar outros horários.")
        if horario not in sessao.get("horarios_livres", []):
            return responder("Esse horário não está disponível. Escolha um destes: " + ", ".join(sessao.get("horarios_livres", [])) + ". Você também pode informar outra data, como 16/10/2026, ou cancelar.")
        sessao["horario"] = horario
        sessao["etapa"] = "paciente"
        return responder("Perfeito. Qual é o nome completo do paciente?")

    if etapa == "paciente":
        nome = texto.strip()
        if len(nome) < 2 or nome.isdigit():
            return responder("Pode informar o nome do paciente, por favor?")
        sessao["paciente"] = nome
        data_br = datetime.strptime(sessao["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
        sessao["etapa"] = "confirmacao"
        return responder(f"Confirme os dados: {sessao['especialidade'].replace('_',' ').title()}, {data_br} às {sessao['horario']}, paciente {nome}. Deseja confirmar?")

    if etapa == "confirmacao":
        if nao(texto):
            sessao.clear()
            return responder("Tudo bem, não fiz o agendamento. Nenhuma consulta foi marcada. Se quiser, podemos começar novamente.")
        if not sim(texto):
            return responder("Para concluir, responda ‘sim’ para confirmar ou ‘não’ para desistir do agendamento.")
        sucesso = criar_agendamento(sessao["paciente"], sessao["medico"], sessao["data"], sessao["horario"])
        if sucesso:
            data_br = datetime.strptime(sessao["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
            resultado = f"Consulta agendada com sucesso! {data_br} às {sessao['horario']}, com {sessao['medico']}."
        else:
            resultado = "Esse horário acabou de ficar indisponível. Vamos escolher outra data."
        sessao.clear()
        return responder(resultado)

    return None

@app.get("/version")
def version():
    return jsonify({"assistente": "Bartho", "api_version": API_VERSION})


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
        sessao = _carregar_sessao(conversa_id)
        texto_normalizado = normalizar(texto)

        # Despedidas e agradecimentos são intenções próprias, não devem cair no fluxo legado.
        if mensagem_encerramento(texto):
            sessao.clear()
            return responder("Tudo bem! Obrigado por conversar com a Clínica Bem. Quando precisar, estarei por aqui. Até a próxima!")
        if mensagem_de_agradecimento(texto):
            return responder("Por nada! Posso ajudar com mais alguma coisa?")

        # Remarcar/cancelar consulta já marcada. Se a pessoa estiver apenas montando um
        # agendamento ainda não confirmado, cancelar encerra somente essa solicitação.
        etapa_atual = sessao.get("etapa", "")
        etapas_fluxo_novo = {"especialidade", "apos_lista_especialidades", "confirmar_horarios", "data", "horario", "paciente", "confirmacao"}
        if pedido_remarcacao(texto) and not str(etapa_atual).startswith("remarcar_"):
            return _iniciar_acao_agendamento(sessao, "remarcar")
        if pedido_cancelamento(texto):
            if etapa_atual in etapas_fluxo_novo:
                sessao.clear()
                return responder("Tudo bem, cancelei esta solicitação. Nenhum agendamento novo foi confirmado. Posso ajudar com mais alguma coisa?")
            if str(etapa_atual).startswith("cancelamento_"):
                return processar_fluxo(texto, sessao)
            return _iniciar_acao_agendamento(sessao, "cancelar")

        # Trata convênio informado em mensagem isolada antes de qualquer fluxo legado.
        # Isso evita que "tenho Sul América" caia no processar_mensagem antigo.
        convenio_mencionado = identificar_convenio(texto)
        mensagem_informa_convenio = any(x in texto_normalizado for x in (
            "tenho", "uso", "sou cliente", "meu convenio", "meu plano", "atendo pelo",
            "sou da", "estou na", "estou com"
        ))
        pergunta_sobre_cobertura = (
            "consulta" in texto_normalizado and any(x in texto_normalizado for x in (
                "cobre", "cobertura", "coberto", "coberta", "plano"
            ))
        )
        if convenio_mencionado and mensagem_informa_convenio:
            sessao["convenio_informado"] = convenio_mencionado
            if pergunta_sobre_cobertura:
                return responder(
                    f"Sim, a Clínica Bem atende consultas por {convenio_mencionado}. "
                    "A clínica não realiza exames de sangue nem cirurgias."
                )
            return responder(
                f"Entendi, você tem {convenio_mencionado}. A Clínica Bem atende consultas por esse convênio. "
                "A clínica não realiza exames de sangue nem cirurgias. "
                "Quer saber sobre a cobertura de uma consulta ou deseja agendar?"
            )

        if texto_normalizado in {"oi", "ola", "opa", "bom dia", "boa tarde", "boa noite", "oi tudo bem", "ola tudo bem"} and not sessao.get("etapa"):
            return responder("Olá! 👋 Sou o Bartho, assistente virtual da Clínica Bem. Como posso ajudar?")

        # Um pedido explícito novo inicia fluxo novo.
        if any(x in texto_normalizado for x in (
            "quero agendar", "agendar consulta", "marcar consulta", "quero marcar",
            "gostaria de agendar", "gostaria de marcar", "preciso agendar", "preciso marcar"
        )):
            return iniciar_agendamento(sessao, texto)

        # Se a pessoa acabou de perguntar quais especialidades existem e responde
        # apenas com uma delas (ex.: "Cardiologia"), aproveitamos o contexto para
        # iniciar o agendamento. Esse estado é salvo no SQLite junto com a sessão.
        if sessao.get("etapa") == "apos_lista_especialidades":
            especialidade = extrair_especialidade(texto)
            if especialidade:
                return iniciar_agendamento(sessao, texto)
            # Não prende a conversa a esse contexto se a próxima mensagem for outra coisa.
            sessao.clear()

        # Reconhece quando a pessoa informa o próprio convênio, sem repetir a FAQ
        # genérica como se ela tivesse perguntado novamente quais planos são aceitos.
        convenio_mencionado = identificar_convenio(texto)
        mensagem_informa_convenio = any(x in texto_normalizado for x in (
            "tenho", "uso", "sou cliente", "meu convenio", "meu plano", "atendo pelo"
        ))
        pergunta_sobre_cobertura = any(x in texto_normalizado for x in (
            "cobre consulta", "cobre a consulta", "consulta cobre", "cobertura", "meu plano cobre", "cobre"
        )) and "consulta" in texto_normalizado
        if convenio_mencionado and mensagem_informa_convenio and not pergunta_sobre_cobertura and not any(
            x in texto_normalizado for x in ("quais convenios", "quais planos", "aceita convenio", "aceita convenios")
        ):
            sessao["convenio_informado"] = convenio_mencionado
            return responder(
                f"Entendi, você tem {convenio_mencionado}. A Clínica Bem atende consultas por esse convênio, "
                "conforme as condições do plano. A clínica não realiza exames de sangue nem cirurgias. "
                "Você quer saber sobre a cobertura ou deseja agendar uma consulta?"
            )
        elif convenio_mencionado and mensagem_informa_convenio:
            # Guarda o plano e deixa a FAQ responder à pergunta que veio na mesma mensagem.
            sessao["convenio_informado"] = convenio_mencionado

        # FAQ e escolha de especialidade funcionam mesmo fora do roteiro exato.
        # Se a mensagem trouxer uma especialidade, inicia/retoma a escolha sem exigir
        # a frase literal “Quero agendar uma consulta”.
        resposta_faq = faq(texto, sessao.get("convenio_informado"))
        if resposta_faq:
            if pergunta_especialidades(texto):
                sessao["etapa"] = "especialidade"
            return responder(resposta_faq)

        if sessao.get("etapa"):
            resultado = processar_fluxo(texto, sessao)
            if resultado is not None:
                return resultado

        especialidade = extrair_especialidade(texto)
        if especialidade:
            return iniciar_agendamento(sessao, texto)

        if pergunta_horarios(texto):
            sessao["etapa"] = "especialidade"
            return responder("Claro! Para consultar os horários, primeiro me diga qual especialidade você procura: Cardiologia, Dermatologia ou Clínica Geral.")

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
            resposta = "Posso ajudar com convênios, especialidades, horários, agendamento, remarcação ou cancelamento. O que você gostaria de fazer?"
        if "vamos começar pelo botão de agendamento" in normalizar(resposta) or "escreva quero agendar uma consulta" in normalizar(resposta):
            resposta = "Posso ajudar com convênios, especialidades, horários, agendamento, remarcação ou cancelamento. O que você gostaria de fazer?"
        return jsonify({"resposta": resposta, "resultado": resultado})
    except PerguntaInterativaWeb as pergunta:
        # O fluxo de terminal não pode continuar seu call stack entre requisições HTTP.
        # Não devolver a mensagem enganosa de que a pessoa precisa recomeçar pelo botão.
        app.logger.warning("Fluxo legado solicitou entrada web sem estado de continuação: %s", pergunta.pergunta)
        return jsonify({
            "resposta": "Posso ajudar com convênios, especialidades, horários, agendamento, remarcação ou cancelamento. O que você gostaria de fazer?",
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
