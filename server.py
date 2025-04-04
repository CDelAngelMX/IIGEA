from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import requests
import json
import threading
import time
import xml.etree.ElementTree as ET
import logging
from datetime import datetime, timedelta

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Reducir logs de SocketIO
socketio_logger = logging.getLogger('socketio.server')
socketio_logger.setLevel(logging.WARNING)
engineio_logger = logging.getLogger('engineio.server')
engineio_logger.setLevel(logging.WARNING)

app = Flask(__name__, template_folder='templates')
CORS(app, resources={r"/*": {"origins": "*", "allow_headers": ["Content-Type", "Authorization", "Access-Control-Allow-Credentials"], "supports_credentials": True}})
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True, async_mode='threading')

# Global variables
messages = []
last_identifier = None  # Modificación clave: Usar identifier en lugar de tiempo
last_fetch_time = datetime.now() - timedelta(seconds=2)

# Función auxiliar para obtener texto de manera segura
def get_text(element, xpath):
    """Obtiene el texto de un elemento XML de manera segura."""
    node = element.find(xpath)
    return node.text if node is not None else None

# Function to fetch RSS feed
def fetch_rss():
    global messages, last_identifier, last_fetch_time
    while True:
        try:
            current_time = datetime.now()
            # Consultar el RSS cada segundo
            if (current_time - last_fetch_time).total_seconds() >= 1:
                last_fetch_time = current_time
                try:
                    logger.info("=== Iniciando obtención de RSS ===")
                    response = requests.get(
                        'https://rss.sasmex.net/api/v1/alerts/latest/cap/',
                        timeout=2,
                        headers={'Accept': 'application/xml'}
                    )
                    if response.status_code == 200:
                        logger.info("=== Respuesta RSS recibida ===")
                        try:
                            root = ET.fromstring(response.content)
                            logger.info("=== XML parseado correctamente ===")
                            entries = root.findall('{http://www.w3.org/2005/Atom}entry')
                            logger.info(f"=== Encontradas {len(entries)} entradas ===")
                            for entry in entries:
                                try:
                                    # Obtener campos básicos
                                    title = entry.find('{http://www.w3.org/2005/Atom}title')
                                    updated = entry.find('{http://www.w3.org/2005/Atom}updated')
                                    alert_content = entry.find('{http://www.w3.org/2005/Atom}content')
                                    if title is None or updated is None or alert_content is None:
                                        logger.warning("Entrada incompleta, saltando...")
                                        continue
                                    title_text = title.text if title is not None else ""
                                    updated_text = updated.text if updated is not None else ""
                                    # Convertir updated a datetime para comparación
                                    try:
                                        updated_dt = datetime.fromisoformat(updated_text.replace('Z', '+00:00'))
                                    except (ValueError, TypeError):
                                        logger.warning(f"Error al convertir fecha: {updated_text}")
                                        continue
                                    # Obtener contenido CAP
                                    alert = alert_content.find('{urn:oasis:names:tc:emergency:cap:1.1}alert')
                                    if alert is None:
                                        logger.warning("No se encontró el contenido CAP, saltando...")
                                        continue
                                    # Obtener identifier
                                    current_identifier = get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}identifier')
                                    if not current_identifier:
                                        logger.warning("Alerta sin identifier, saltando...")
                                        continue
                                    # Verificar si ya procesamos este identifier
                                    if current_identifier == last_identifier:
                                        logger.info(f"Identifier {current_identifier} ya procesado")
                                        continue
                                    # Construir mensaje usando get_text
                                    message = {
                                        'title': title_text,
                                        'updated': updated_text,
                                        'identifier': current_identifier,
                                        'sender': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}sender'),
                                        'sent': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}sent'),
                                        'status': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}status'),
                                        'msgType': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}msgType'),
                                        'source': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}source'),
                                        'scope': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}scope'),
                                        'code': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}code'),
                                        'note': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}note'),
                                        'references': get_text(alert, '{urn:oasis:names:tc:emergency:cap:1.1}references'),
                                        'info': []
                                    }
                                    # Obtener información detallada
                                    info = alert.find('{urn:oasis:names:tc:emergency:cap:1.1}info')
                                    if info is not None:
                                        info_data = {
                                            'language': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}language'),
                                            'category': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}category'),
                                            'event': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}event'),
                                            'responseType': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}responseType'),
                                            'urgency': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}urgency'),
                                            'severity': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}severity'),
                                            'certainty': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}certainty'),
                                            'audience': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}audience'),
                                            'eventCode': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}eventCode'),
                                            'effective': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}effective'),
                                            'onset': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}onset'),
                                            'expires': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}expires'),
                                            'senderName': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}senderName'),
                                            'headline': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}headline'),
                                            'description': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}description'),
                                            'instruction': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}instruction'),
                                            'web': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}web'),
                                            'contact': get_text(info, '{urn:oasis:names:tc:emergency:cap:1.1}contact')
                                        }
                                        message['info'].append(info_data)
                                    # Emitir mensaje al WebSocket
                                    socketio.emit('new_message', message)
                                    logger.info(f"=== Mensaje enviado al WebSocket: {message['title']} ===")
                                    # Actualizar estado
                                    last_identifier = current_identifier
                                    messages.append(message)
                                except Exception as e:
                                    logger.error(f"Error al procesar entrada RSS: {str(e)}")
                                    continue
                        except ET.ParseError as e:
                            logger.error(f"Error al parsear XML: {str(e)}")
                            logger.error(f"Contenido de la respuesta: {response.text[:500]}...")
                            time.sleep(1)
                            continue
                    else:
                        logger.warning(f"Error al obtener el RSS. Código de estado: {response.status_code}")
                        logger.error(f"Contenido de la respuesta: {response.text[:500]}...")
                        time.sleep(1)
                except requests.exceptions.RequestException as e:
                    logger.error(f"Error en la solicitud HTTP: {str(e)}")
                    time.sleep(1)
                    continue
        except Exception as e:
            logger.error(f"Error crítico: {str(e)}")
            time.sleep(1)
        # Pequeño delay para no sobrecargar el CPU
        time.sleep(0.1)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/messages', methods=['GET'])
def get_messages():
    return jsonify({'messages': messages})

# Manejador de eventos para la conexión de WebSocket
@socketio.on('connect')
def handle_connect():
    logger.info(f"Cliente conectado: {request.sid}")

if __name__ == '__main__':
    logger.info("=== Iniciando servidor ===")
    socketio.start_background_task(fetch_rss)
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
