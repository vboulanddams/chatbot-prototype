"""
Prototype de chatbot LLM-first pour l'IVR telephonie mobile.

Principe :
- Les scripts (mode operatoire / gestion d'erreur / FAQ) sont charges depuis knowledge_base.json
- Chaque message utilisateur est envoye a Gemini avec ces scripts en contexte
- Gemini ne doit repondre qu'a partir de ces scripts (garde-fou dans les instructions systeme)
- Quand un sujet n'est couvert par aucun script, le modele l'indique via un marqueur invisible
  que le backend detecte, enregistre dans sujets_non_couverts.json, puis retire avant
  d'afficher la reponse a l'utilisateur.

Lancer :
    pip install -r requirements.txt
    set GOOGLE_API_KEY=votre_cle      (Windows PowerShell : $env:GOOGLE_API_KEY="votre_cle")
    python backend.py

Puis ouvrir http://localhost:5000
Journal des sujets non couverts : http://localhost:5000/admin/sujets-non-couverts
"""

import json
import os
import re
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
import google.generativeai as genai

app = Flask(__name__, static_folder="static")

genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

with open("knowledge_base.json", "r", encoding="utf-8") as f:
    KNOWLEDGE_BASE = json.load(f)

UNCOVERED_TOPICS_FILE = "sujets_non_couverts.json"

# Historique de conversation en memoire, par session (prototype uniquement, pas pour la prod)
CONVERSATIONS = {}

# Le modele doit inserer ce marqueur en toute fin de reponse quand le sujet n'est pas couvert.
# Format attendu : [SUJET_NON_COUVERT: resume court du sujet]
UNCOVERED_MARKER_PATTERN = re.compile(r"\[SUJET_NON_COUVERT:\s*(.*?)\]\s*$", re.IGNORECASE | re.DOTALL)


def build_system_instruction():
    """Construit les instructions systeme a partir de la base de connaissances complete :
    instructions generales (introduction, personnalite, regles), note MDM transversale,
    regle de cloture, et l'ensemble des scripts par sujet/plateforme."""
    kb = KNOWLEDGE_BASE

    instructions_generales = json.dumps(kb.get("instructions_generales", {}), ensure_ascii=False, indent=2)
    note_mdm = json.dumps(kb.get("note_mdm", {}), ensure_ascii=False, indent=2)
    cloture = json.dumps(kb.get("cloture_conversation", {}), ensure_ascii=False, indent=2)
    scripts_text = json.dumps(kb.get("scripts", []), ensure_ascii=False, indent=2)

    return f"""Tu es VBOT NOVA, l'assistant virtuel de support technique mobile de Dam's decrit ci-dessous. Applique ces instructions a la lettre. Si l'utilisateur te demande ton nom, reponds VBOT NOVA.

REGLE ABSOLUE DE LANGUE, PRIORITAIRE SUR TOUT LE RESTE :
Tout le contenu ci-dessous (instructions, scripts, exemples) est redige en francais, mais c'est uniquement ta source d'information interne, PAS la langue de reponse a utiliser par defaut. Tu dois TOUJOURS repondre dans la langue exacte utilisee par l'utilisateur dans son dernier message, quelle qu'elle soit (anglais, espagnol, etc.), meme si cela signifie traduire toi-meme le contenu francais des scripts. Ne traduis jamais le message de l'utilisateur vers le francais avant d'y repondre : comprends-le dans sa langue d'origine et reponds directement dans cette meme langue. Si l'utilisateur ecrit en anglais, ta reponse entiere doit etre en anglais, sans aucun mot de francais. Si l'utilisateur change de langue en cours de conversation, adapte-toi immediatement.

IMPORTANT : le message d'introduction ("Bienvenue chez Dam's...") a deja ete affiche a l'utilisateur par l'interface AVANT le debut de cette conversation. Ne le repete JAMAIS, meme au premier message. Reponds directement et uniquement a la demande de l'utilisateur.

INSTRUCTIONS GENERALES (personnalite, ton, regles de conversation, restrictions) :
{instructions_generales}

NOTE TRANSVERSALE SUR LES TERMINAUX ENROLES MDM (a appliquer a tous les scripts concernes) :
{note_mdm}

REGLE DE CLOTURE DE CONVERSATION (a appliquer une fois un script termine) :
{cloture}

REGLES STRICTES SUPPLEMENTAIRES :
1. Tu ne reponds QU'a partir des scripts fournis ci-dessous. Si un sujet n'est pas couvert, dis-le clairement a l'utilisateur et propose de transferer a un operateur physique.
2. Ne jamais inventer une procedure, un code ou un parametrage qui ne figure pas dans les scripts.
3. Identifie d'abord la plateforme (Android ou Apple) et, si besoin, la marque, avant de choisir le script a suivre.
4. Applique systematiquement la note MDM transversale ci-dessus pour tout script marque "note_mdm_applicable": true.
5. Deroule les scripts etape par etape, une question ou une manipulation a la fois, en attendant la confirmation de l'utilisateur avant de continuer (voir instructions generales).
6. A la fin d'un script, applique la regle de cloture de conversation ci-dessus.

REGLE DE JOURNALISATION DES SUJETS NON COUVERTS (tres importante, a respecter systematiquement) :
Si la demande de l'utilisateur ne correspond a AUCUN script de la base (marque, systeme, ou sujet absent), tu dois :
a) repondre normalement a l'utilisateur (l'informer que ce sujet n'est pas encore pris en charge, proposer le transfert a un operateur physique) ;
b) puis, apres ta reponse a l'utilisateur, ajouter sur une derniere ligne separee, EXACTEMENT dans ce format, sans rien autour :
[SUJET_NON_COUVERT: resume tres court du sujet demande, quelques mots]
Ce marqueur est technique, il ne doit jamais etre mentionne a l'utilisateur ni explique, il sera retire automatiquement avant affichage. Ne l'ajoute QUE si le sujet n'est vraiment couvert par aucun script ci-dessous.

SCRIPTS DISPONIBLES :
{scripts_text}
"""


def get_model():
    return genai.GenerativeModel(
        model_name="gemini-3.6-flash",
        system_instruction=build_system_instruction(),
    )


def extract_and_log_uncovered_topic(session_id, user_message, assistant_reply):
    """Detecte le marqueur [SUJET_NON_COUVERT: ...] en fin de reponse, l'enregistre dans
    le journal JSON, et renvoie la reponse nettoyee (sans le marqueur) a afficher a l'utilisateur."""
    match = UNCOVERED_MARKER_PATTERN.search(assistant_reply.strip())
    if not match:
        return assistant_reply

    topic_summary = match.group(1).strip()
    cleaned_reply = assistant_reply[:match.start()].rstrip()

    entry = {
        "date": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "sujet": topic_summary,
        "message_utilisateur": user_message,
    }

    try:
        if os.path.exists(UNCOVERED_TOPICS_FILE):
            with open(UNCOVERED_TOPICS_FILE, "r", encoding="utf-8") as f:
                topics = json.load(f)
        else:
            topics = []
        topics.append(entry)
        with open(UNCOVERED_TOPICS_FILE, "w", encoding="utf-8") as f:
            json.dump(topics, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erreur lors de l'enregistrement du sujet non couvert : {e}")

    return cleaned_reply


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    session_id = data.get("session_id", "default")
    user_message = data.get("message", "")

    if session_id not in CONVERSATIONS:
        CONVERSATIONS[session_id] = []

    model = get_model()
    chat_session = model.start_chat(history=CONVERSATIONS[session_id])

    response = chat_session.send_message(user_message)
    raw_reply = response.text

    assistant_reply = extract_and_log_uncovered_topic(session_id, user_message, raw_reply)

    # On sauvegarde l'historique au format attendu par Gemini pour le prochain appel
    CONVERSATIONS[session_id] = [
        {"role": msg.role, "parts": [part.text for part in msg.parts]}
        for msg in chat_session.history
    ]

    return jsonify({"reply": assistant_reply})


@app.route("/api/reset", methods=["POST"])
def reset():
    data = request.get_json()
    session_id = data.get("session_id", "default")
    CONVERSATIONS[session_id] = []
    return jsonify({"status": "ok"})


@app.route("/api/sujets-non-couverts", methods=["GET"])
def get_uncovered_topics():
    """Retourne la liste brute des sujets non couverts (JSON), utilisable par un futur export."""
    if os.path.exists(UNCOVERED_TOPICS_FILE):
        with open(UNCOVERED_TOPICS_FILE, "r", encoding="utf-8") as f:
            topics = json.load(f)
    else:
        topics = []
    return jsonify(topics)


@app.route("/admin/sujets-non-couverts", methods=["GET"])
def admin_uncovered_topics():
    """Page simple listant les sujets non couverts, pour reperer les scripts a creer en priorite."""
    if os.path.exists(UNCOVERED_TOPICS_FILE):
        with open(UNCOVERED_TOPICS_FILE, "r", encoding="utf-8") as f:
            topics = json.load(f)
    else:
        topics = []

    # Comptage par sujet (regroupement simple sur le texte exact du resume) pour reperer les recurrences
    counts = {}
    for t in topics:
        counts[t["sujet"]] = counts.get(t["sujet"], 0) + 1

    rows = ""
    for t in reversed(topics):  # plus recent en premier
        rows += f"""
        <tr>
          <td>{t['date'][:19].replace('T', ' ')}</td>
          <td><strong>{t['sujet']}</strong> <span class="count">({counts[t['sujet']]}x au total)</span></td>
          <td>{t['message_utilisateur']}</td>
        </tr>"""

    html = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
      <meta charset="UTF-8">
      <title>Sujets non couverts</title>
      <style>
        body {{ font-family: -apple-system, sans-serif; background: #0f1115; color: #e8e9ed; padding: 30px; }}
        h1 {{ font-size: 20px; }}
        p.meta {{ color: #8b8f9a; font-size: 13px; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
        th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #2a2e38; font-size: 14px; vertical-align: top; }}
        th {{ color: #8b8f9a; font-weight: 600; text-transform: uppercase; font-size: 11px; }}
        .count {{ color: #8b8f9a; font-size: 12px; }}
        .empty {{ color: #8b8f9a; margin-top: 30px; }}
      </style>
    </head>
    <body>
      <h1>Bibliotheque des sujets non couverts</h1>
      <p class="meta">{len(topics)} demande(s) enregistree(s) sur des sujets absents de la base de connaissances.</p>
      {"<table><tr><th>Date</th><th>Sujet</th><th>Message de l'utilisateur</th></tr>" + rows + "</table>" if topics else "<p class='empty'>Aucun sujet non couvert enregistre pour l'instant.</p>"}
    </body>
    </html>
    """
    return html


if __name__ == "__main__":
    # En local (votre PC), Render ne fournit pas de variable PORT : on retombe alors sur 5000.
    # En ligne (Render, ou tout hebergement similaire), on doit ecouter sur 0.0.0.0 et sur le
    # port impose par la plateforme, sinon le service n'est jamais detecte comme actif.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
