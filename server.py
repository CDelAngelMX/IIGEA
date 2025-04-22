import eventlet
import json

# Parcheo de eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import requests
import xml.etree.ElementTree as ET
import logging
from datetime import datetime, timedelta

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('socketio.server').setLevel(logging.WARNING)
logging.getLogger('engineio.server').setLevel(logging.WARNING)

# Flask y SocketIO
app = Flask(__name__, template_folder='templates')
CORS(app, resources={r"/*": {"origins": "*"}})
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", logger=False, engineio_logger=False, async_mode='eventlet')

# Variables globales
messages = []
last_identifier = None
last_fetch_time = datetime.now() - timedelta(seconds=2)

# Firebase Cloud Messaging
FCM_SERVER_KEY = "A07rSIAKesjrScKPzgqxaVRz-gg9iozEZpuyI2GHXXw"
FCM_URL = "https://fcm.googleapis.com/fcm/send"

# Telegram
TELEGRAM_CHAT_ID = "6134394569"
TELEGRAM_BOT_TOKEN = "7659663435:AAESZfoSnX-7F4d44_lgBotKRK3uEymeINY"

def notificar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}, timeout=5)
    except Exception as e:
        logger.error(f"Telegram fallo: {e}")

def send_to_fcm(message):
    """
    Envía un mensaje a Firebase Cloud Messaging (FCM).
    :param message: Diccionario con los datos del mensaje.
    """
    headers = {
        "Authorization": f"key={FCM_SERVER_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "to": "/topics/sismos",  # Envía a todos los usuarios suscritos al tema "sismos"
        "priority": "high",
        "data": {
            "id": message.get("id", ""),
            "title": message.get("title", "Alerta Sísmica"),
            "sent": message.get("sent", ""),
            "severity": message.get("severity", ""),
            "description": message.get("description", ""),
            "circle": message.get("circle", "")
        }
    }
    try:
        response = requests.post(FCM_URL, headers=headers, data=json.dumps(payload), timeout=5)
        if response.status_code == 200:
            logger.info("Mensaje enviado exitosamente a FCM")
        else:
            logger.error(f"Error al enviar mensaje a FCM: {response.status_code}, {response.text}")
    except Exception as e:
        logger.error(f"Excepción al enviar mensaje a FCM: {str(e)}")

# Función auxiliar para obtener texto de manera segura
def get_text(element, xpath, ns, default=None):
    node = element.find(xpath, ns)
    return node.text if node is not None and node.text else default

# Espacios de nombres
ns = {
    'atom': 'http://www.w3.org/2005/Atom',
    'cap': 'urn:oasis:names:tc:emergency:cap:1.1'
}

def fetch_rss():
    global messages, last_identifier, last_fetch_time
    errores = 0
    notificado = False
    heartbeat = 0

    while True:
        try:
            current_time = datetime.now()
            if (current_time - last_fetch_time).total_seconds() >= 1:
                last_fetch_time = current_time
                logger.info("🚀 Obteniendo RSS...")
                try:
                    response = requests.get(
                        'https://rss.sasmex.net/api/v1/alerts/latest/cap/',
                        timeout=5,
                        headers={'Accept': 'application/xml'}
                    )
                    if response.status_code == 200:
                        root = ET.fromstring(response.content)
                        logger.info("=== XML parseado correctamente ===")
                        entries = root.findall('atom:entry', ns)
                        logger.info(f"📥 {len(entries)} entradas encontradas")

                        for entry in entries:
                            try:
                                entry_id = get_text(entry, 'atom:id', ns)
                                title = get_text(entry, 'atom:title', ns)
                                updated = get_text(entry, 'atom:updated', ns)
                                content = entry.find('atom:content', ns)

                                if not entry_id or not title or not content:
                                    logger.warning("⚠️ Entrada incompleta, saltando...")
                                    continue

                                if entry_id == last_identifier:
                                    logger.info(f"Identifier {entry_id} ya procesado")
                                    continue

                                if content.text is None or not content.text.strip():
                                    if not list(content):
                                        logger.warning("⚠️ El campo <content> está vacío o no contiene XML. Saltando esta entrada.")
                                        continue

                                try:
                                    inner_xml = ET.tostring(content[0], encoding='unicode')
                                    content_xml = ET.fromstring(inner_xml)
                                except ET.ParseError as e:
                                    logger.error(f"❌ Error al parsear <content>: {str(e)}")
                                    continue

                                alert = content_xml  # Ya es el <alert>

                                sent = get_text(alert, 'cap:sent', ns)

                                msg = {
                                    'id': entry_id,
                                    'title': title,
                                    'sent': sent,
                                    'identifier': get_text(alert, 'cap:identifier', ns),
                                    'msgType': get_text(alert, 'cap:msgType', ns),
                                    'severity': '',
                                    'description': '',
                                    'info': []
                                }

                                info = alert.find('cap:info', ns)
                                if info is not None:
                                    msg['severity'] = get_text(info, 'cap:severity', ns)
                                    msg['description'] = get_text(info, 'cap:description', ns)

                                    circle = info.find('cap:area/cap:circle', ns)
                                    if circle is not None and circle.text:
                                        msg['circle'] = circle.text

                                    msg["info"] = [{
                                        "severity": msg["severity"],
                                        "description": msg["description"],
                                        "circle": msg.get("circle", "")
                                    }]

                                socketio.emit('new_message', msg)
                                logger.info(f"🛠 Identifier nuevo detectado y procesado: {entry_id}")
                                notificar_telegram(f"🔔 Alerta sísmica: {title}")
                                send_to_fcm(msg)  # Enviar mensaje a FCM
                                last_identifier = entry_id
                                messages.append(msg)

                                if len(messages) > 1000:
                                    messages = messages[-500:]

                            except Exception as e:
                                logger.error(f"Error al procesar entrada: {e}")
                    else:
                        errores += 1
                        logger.warning(f"Status: {response.status_code}")
                        logger.error(f"Contenido de la respuesta: {response.text[:500]}...")
                        if errores >= 3 and not notificado:
                            notificar_telegram("⚠️ Falla RSS x3")
                            notificado = True
                        continue
                except requests.exceptions.RequestException as e:
                    errores += 1
                    logger.error(f"Error en la solicitud HTTP: {str(e)}")
                    if errores >= 3 and not notificado:
                        notificar_telegram("⚠️ Falla RSS x3")
                        notificado = True
            heartbeat += 1
            if heartbeat % 300 == 0:
                logger.info("✅ fetch_rss activo")
        except Exception as e:
            logger.error(f"Error crítico: {str(e)}")
        socketio.sleep(0.1)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/messages', methods=['GET'])
def get_messages():
    return jsonify({'messages': messages})

@socketio.on('connect')
def handle_connect():
    logger.info(f"Cliente conectado: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Cliente desconectado: {request.sid}")

@socketio.on('mensaje_simulado')
def handle_mensaje_simulado(data):
    logger.info(f"🧪 Mensaje simulado recibido: {data}")
    emit('new_message', data, broadcast=True)

    # Enviar el mensaje simulado a Telegram
    try:
        mensaje_telegram = f"🔔 Simulación: {data.get('title', 'Sin título')}"
        notificar_telegram(mensaje_telegram)
        logger.info("Mensaje simulado enviado a Telegram.")
    except Exception as e:
        logger.error(f"Fallo al enviar mensaje simulado a Telegram: {e}")
        
    return {'status': 'ok'}

if __name__ == '__main__':
    notificar_telegram("✅ server.py iniciado")
    socketio.start_background_task(fetch_rss)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
