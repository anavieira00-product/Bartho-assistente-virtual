import os
from contextlib import redirect_stdout
from io import StringIO

from flask import Flask, request, jsonify, session
from flask_cors import CORS

# A API roda em modo web: o Bartho não pode bloquear esperando input() no terminal.
os.environ["BARTHO_MODO_WEB"] = "1"

from bartho import processar_mensagem, PerguntaInterativaWeb

app = Flask(__name__)
app.secret_key = os.environ.get("BARTHO_SECRET_KEY", "")
# The current frontend sends no cookies, so credentials are not required.
# Set BARTHO_ALLOWED_ORIGIN to the GitHub Pages origin for a tighter policy.
origem_permitida = os.environ.get("BARTHO_ALLOWED_ORIGIN", "*")
CORS(app, resources={r"/*": {"origins": origem_permitida}})


@app.get("/health")
def health():
    return jsonify({"assistente": "Bartho", "status": "ok"})


@app.post("/mensagem")
def mensagem():
    dados = request.get_json(silent=True) or {}
    texto = str(dados.get("mensagem", "")).strip()

    if not texto:
        return jsonify({"erro": "Mensagem não informada."}), 400

    captura = StringIO()
    try:
        with redirect_stdout(captura):
            resultado = processar_mensagem(texto)

        saida = captura.getvalue().strip()
        resposta = ""

        # Prioriza uma pergunta ou resposta impressa pelo Bartho.
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
        # O fluxo pediu uma resposta do usuário. Retorna a pergunta à interface,
        # em vez de deixar a requisição falhar com erro 500.
        return jsonify({
            "resposta": pergunta.pergunta or "Pode me informar?",
            "aguardando_resposta": True
        })

    except Exception as erro:
        app.logger.exception("Erro ao processar mensagem do Bartho")
        return jsonify({
            "erro": "Não consegui processar sua mensagem. Tente novamente em instantes."
        }), 500


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    print(f"Bartho API iniciada na porta {porta}")
    app.run(host="0.0.0.0", port=porta, debug=False)
