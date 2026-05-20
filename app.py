"""
Telegram Bot Service - микросервис для отправки сообщений через Telegram Bot API.
Разворачивается на Render.com для обхода блокировки Telegram API.
"""
from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Конфигурация
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8610099496:AAHfZIdVbiRF1exnrMq5N88YxD4T0Tkrefw')
API_SECRET = os.environ.get('API_SECRET', 'your-secret-key-here')  # Для защиты API

# Хранилище chat_id (в production лучше использовать Redis или БД)
# Формат: {phone_number: chat_id}
chat_ids = {}


def send_telegram_message(chat_id: int, text: str) -> dict:
    """Отправляет сообщение через Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        return {
            'success': response.status_code == 200,
            'status_code': response.status_code,
            'response': response.json() if response.status_code == 200 else response.text
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'service': 'Telegram Bot Service',
        'version': '1.0.0'
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})


@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """
    Принимает webhook от Telegram.
    Сохраняет chat_id когда пользователь пишет боту.
    """
    data = request.get_json()
    
    message = data.get('message')
    if not message:
        return jsonify({'ok': True})
    
    chat_id = message.get('chat', {}).get('id')
    text = message.get('text', '')
    
    if not chat_id:
        return jsonify({'ok': True})
    
    # Обработка команды /start
    if text.startswith('/start'):
        parts = text.split()
        if len(parts) > 1:
            phone = parts[1]  # /start +79093006036
            chat_ids[phone] = chat_id
            
            # Отправляем подтверждение
            send_telegram_message(
                chat_id,
                f"✅ Номер <code>{phone}</code> привязан!\n\n"
                f"Теперь вы будете получать коды подтверждения здесь."
            )
        else:
            send_telegram_message(
                chat_id,
                "👋 Привет! Я бот для отправки кодов подтверждения.\n\n"
                "Чтобы привязать номер:\n"
                "1. Зайдите на сайт и запросите код\n"
                "2. Отправьте мне команду: <code>/start +79991234567</code>\n\n"
                "(замените +79991234567 на ваш номер)"
            )
    
    return jsonify({'ok': True})


@app.route('/send-code', methods=['POST'])
def send_code():
    """
    API для отправки кода подтверждения.
    Требует API_SECRET для авторизации.
    """
    # Проверка секретного ключа
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    phone = data.get('phone')
    code = data.get('code')
    
    if not phone or not code:
        return jsonify({'error': 'Phone and code are required'}), 400
    
    # Ищем chat_id по номеру телефона
    chat_id = chat_ids.get(phone)
    if not chat_id:
        return jsonify({
            'error': 'Phone not linked to Telegram',
            'message': 'Пользователь не привязал номер к Telegram. Сначала нужно отправить /start +номер боту.'
        }), 404
    
    # Формируем сообщение
    message = (
        f"🔐 <b>Код подтверждения</b>\n\n"
        f"Ваш код для входа: <code>{code}</code>\n\n"
        f"⏳ Код действителен 10 минут.\n"
        f"Если вы не запрашивали код, проигнорируйте это сообщение."
    )
    
    # Отправляем сообщение
    result = send_telegram_message(chat_id, message)
    
    if result['success']:
        return jsonify({
            'success': True,
            'message': 'Code sent successfully'
        })
    else:
        return jsonify({
            'error': 'Failed to send message',
            'details': result.get('error') or result.get('response')
        }), 500


@app.route('/check-phone/<phone>', methods=['GET'])
def check_phone(phone: str):
    """Проверяет, привязан ли номер к Telegram"""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    chat_id = chat_ids.get(phone)
    return jsonify({
        'phone': phone,
        'linked': chat_id is not None,
        'chat_id': chat_id if chat_id else None
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
