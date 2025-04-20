import eventlet

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

# Telegram
TELEGRAM_CHAT_ID = "6134394569"
TELEGRAM_BOT_TOKEN = "7659663435:AAESZfoSnX-7F4d44_lgBotKRK3uEymeINY"

def notificar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}, timeout=5)
    except Exception as e:
        logger.error(f"Telegram fallo: {e}")

def get_text(element, xpath, ns, default=None):
    node = element.find(xpath, ns)
    return node.text if node is not None and node.text else default

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
                logger.info("\U0001F680 Obteniendo RSS...")
                try:
                    response = requests.get('https://rss.sasmex.net/api/v1/alerts/latest/cap/', timeout=5, headers={'Accept': 'application/xml'})
                    if response.status_code == 200:
                        try:
                            root = ET.fromstring(response.content)
                            logger.info("=== XML parseado correctamente ===")
                            entries = root.findall('atom:entry', ns)
                            logger.info(f"\U0001F4E5 {len(entries)} entradas encontradas")

                            for entry in entries:
                                entry_id = get_text(entry, 'atom:id', ns)
                                title = get_text(entry, 'atom:title', ns)
                                content = entry.find('atom:content', ns)

                                if not entry_id or not title or content is None:
                                    logger.warning("⚠️ Entrada incompleta, saltando...")
                                    continue

                                if entry_id == last_identifier:
                                    logger.info(f"Identifier {entry_id} ya procesado")
                                    continue

                                alert = content.find('cap:alert', ns)
                                if alert is None:
                                    logger.warning("⚠️ No se encontró <alert> dentro de <content>. Saltando esta entrada.")
                                    continue

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

                                socketio.emit('new_message', msg)
                                notificar_telegram(f"\U0001F514 Alerta sísmica: {msg['title']}")
                                messages.append(msg)
                                last_identifier = entry_id

                                if len(messages) > 1000:
                                    messages = messages[-500:]

                                logger.info(f"\U0001F6E0 Identifier nuevo detectado y procesado: {entry_id}")

                        except ET.ParseError as e:
                            logger.error(f"❌ Error al parsear XML: {str(e)}")
                            continue
                    else:
                        errores += 1
                        logger.warning(f"Error al obtener el RSS. Código: {response.status_code}")
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
                    continue

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
    logger.info(f"\U0001F9EA Mensaje simulado recibido: {data}")
    emit('new_message', data, broadcast=True)
    return {'status': 'ok'}

if __name__ == '__main__':
    notificar_telegram("✅ server.py iniciado")
    socketio.start_background_task(fetch_rss)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
