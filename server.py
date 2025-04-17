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

# Configuración básica de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Reducir logs verbosos de socketio/engineio
logging.getLogger('socketio.server').setLevel(logging.WARNING)
logging.getLogger('engineio.server').setLevel(logging.WARNING)

# Flask y SocketIO
app = Flask(__name__, template_folder='templates')
CORS(app, resources={r"/*": {"origins": "*", "allow_headers": ["Content-Type", "Authorization", "Access-Control-Allow-Credentials"], "supports_credentials": True}})
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


def get_text(element, xpath):
    node = element.find(xpath)
    return node.text if node is not None else None


def fetch_rss():
    global messages, last_identifier, last_fetch_time
    errores = 0
    notificado = False
    heartbeat = 0

    while True:
        try:
            if (datetime.now() - last_fetch_time).total_seconds() >= 1:
                last_fetch_time = datetime.now()
                try:
                    logger.info("🛰️ Obteniendo RSS...")
                    resp = requests.get('https://rss.sasmex.net/api/v1/alerts/latest/cap/', timeout=2, headers={'Accept': 'application/xml'})
                    if resp.status_code != 200:
                        errores += 1
                        logger.warning(f"Status: {resp.status_code}")
                        if errores >= 3 and not notificado:
                            notificar_telegram("⚠️ Falla RSS x3")
                            notificado = True
                        continue

                    errores = 0
                    notificado = False
                    root = ET.fromstring(resp.content)
                    entries = root.findall('{http://www.w3.org/2005/Atom}entry')

                    for entry in entries:
                        try:
                            title = entry.find('{http://www.w3.org/2005/Atom}title')
                            updated = entry.find('{http://www.w3.org/2005/Atom}updated')
                            content = entry.find('{http://www.w3.org/2005/Atom}content')
                            if not title or not updated or not content:
                                continue

                            identifier = get_text(content.find('{urn:oasis:names:tc:emergency:cap:1.1}alert'), '{urn:oasis:names:tc:emergency:cap:1.1}identifier')
                            if not identifier or identifier == last_identifier:
                                continue

                            msg = {
                                'title': title.text,
                                'updated': updated.text,
                                'identifier': identifier,
                                'info': []
                            }

                            alert = content.find('{urn:oasis:names:tc:emergency:cap:1.1}alert')
                            if alert is not None:
                                for tag in ['sender','sent','status','msgType','source','scope','code','note','references']:
                                    msg[tag] = get_text(alert, f'{{urn:oasis:names:tc:emergency:cap:1.1}}{tag}')

                                info = alert.find('{urn:oasis:names:tc:emergency:cap:1.1}info')
                                if info is not None:
                                    msg['info'].append({
                                        tag: get_text(info, f'{{urn:oasis:names:tc:emergency:cap:1.1}}{tag}')
                                        for tag in [
                                            'language','category','event','responseType','urgency','severity','certainty',
                                            'audience','eventCode','effective','onset','expires','senderName','headline',
                                            'description','instruction','web','contact']
                                    })

                            socketio.emit('new_message', msg)
                            last_identifier = identifier
                            messages.append(msg)
                            if len(messages) > 1000:
                                messages = messages[-500:]
                            notificar_telegram(f"🔔 Alerta sísmica: {title.text}")
                        except Exception as e:
                            logger.error(f"Error procesando entrada: {e}")
                except Exception as e:
                    errores += 1
                    logger.error(f"Fallo petición RSS: {e}")
                    if errores >= 3 and not notificado:
                        notificar_telegram("⚠️ Falla RSS x3")
                        notificado = True

            heartbeat += 1
            if heartbeat % 600 == 0:
                logger.info("✅ fetch_rss activo")

        except Exception as e:
            logger.critical(f"Error crítico en fetch_rss: {e}")

        socketio.sleep(0.1)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/messages', methods=['GET'])
def get_messages():
    return jsonify({'messages': messages})


@socketio.on('connect')
def conectado():
    logger.info(f"Cliente conectado: {request.sid}")


@socketio.on('disconnect')
def desconectado():
    logger.info(f"Cliente desconectado: {request.sid}")


socketio.on('mensaje_simulado')
def handle_mensaje_simulado(data):
    print(f"🧪 Mensaje simulado recibido: {data}")
    emit('new_message', data, broadcast=True)
    return {'status': 'ok'}  # <-- Esto es el ACK que recibe el emisor


if __name__ == '__main__':
    notificar_telegram("✅ server.py iniciado")
    socketio.start_background_task(fetch_rss)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
