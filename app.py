"""
Telegram Bot Service - микросервис для отправки сообщений через Telegram Bot API.
Разворачивается на Render.com для обхода блокировки Telegram API.

Новые функции:
- Регистрация через кнопку "Передать контакт"
- Восстановление пароля через Telegram
"""
from flask import Flask, request, jsonify
import requests
import os
import secrets
import hashlib
import time
from datetime import datetime, timedelta

app = Flask(__name__)

# Конфигурация
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8610099496:AAHfZIdVbiRF1exnrMq5N88YxD4T0Tkrefw')
API_SECRET = os.environ.get('API_SECRET', 'your-secret-key-here')  # Для защиты API
WEBAPP_URL = os.environ.get('WEBAPP_URL', 'https://your-domain.com')  # URL фронтенда

# Хранилище (в production лучше использовать Redis или БД)
# Формат: {phone_number: chat_id}
chat_ids = {}

# Временные токены для регистрации
# Формат: {token: {'phone': str, 'chat_id': int, 'expires': datetime}}
registration_tokens = {}

# Токены для сброса пароля
# Формат: {token: {'phone': str, 'chat_id': int, 'expires': datetime, 'code': str}}
password_reset_tokens = {}


def generate_token() -> str:
    """Генерирует случайный токен"""
    return secrets.token_urlsafe(32)


def cleanup_expired_tokens():
    """Очищает просроченные токены"""
    now = datetime.now()
    expired_reg = [t for t, data in registration_tokens.items() if data['expires'] < now]
    expired_reset = [t for t, data in password_reset_tokens.items() if data['expires'] < now]
    for t in expired_reg:
        del registration_tokens[t]
    for t in expired_reset:
        del password_reset_tokens[t]


def send_telegram_message(chat_id: int, text: str, reply_markup: dict = None) -> dict:
    """Отправляет сообщение через Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup
    
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


def send_telegram_message_with_button(chat_id: int, text: str, button_text: str, url: str) -> dict:
    """Отправляет сообщение с кнопкой-ссылкой"""
    reply_markup = {
        'inline_keyboard': [[{
            'text': button_text,
            'url': url
        }]]
    }
    return send_telegram_message(chat_id, text, reply_markup)


@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'service': 'Telegram Bot Service',
        'version': '2.0.0'
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})


@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """
    Принимает webhook от Telegram.
    Обрабатывает:
    - Команда /start
    - Кнопка "Передать контакт" (contact)
    - Команда /start с токеном для регистрации/восстановления пароля
    """
    data = request.get_json()
    
    message = data.get('message')
    callback_query = data.get('callback_query')
    
    if callback_query:
        # Обработка callback кнопок
        return handle_callback_query(callback_query)
    
    if not message:
        return jsonify({'ok': True})
    
    chat_id = message.get('chat', {}).get('id')
    text = message.get('text', '')
    contact = message.get('contact')
    
    if not chat_id:
        return jsonify({'ok': True})
    
    # Обработка контакта (кнопка "Передать контакт")
    if contact:
        return handle_contact(chat_id, contact)
    
    # Обработка команды /start
    if text.startswith('/start'):
        return handle_start_command(chat_id, text)
    
    return jsonify({'ok': True})


def handle_contact(chat_id: int, contact: dict):
    """Обработка полученного контакта"""
    phone_number = contact.get('phone_number', '')
    first_name = contact.get('first_name', '')
    
    if not phone_number:
        send_telegram_message(
            chat_id,
            "❌ Не удалось получить номер телефона. Попробуйте ещё раз."
        )
        return jsonify({'ok': True})
    
    # Нормализуем номер
    if phone_number.startswith('8') and len(phone_number) == 11:
        phone_number = '+7' + phone_number[1:]
    elif not phone_number.startswith('+'):
        phone_number = '+' + phone_number
    
    # Сохраняем chat_id
    chat_ids[phone_number] = chat_id
    
    # Генерируем токен для регистрации
    token = generate_token()
    registration_tokens[token] = {
        'phone': phone_number,
        'chat_id': chat_id,
        'first_name': first_name,
        'expires': datetime.now() + timedelta(minutes=30)
    }
    
    # Отправляем сообщение с кнопкой для возврата на сайт
    webapp_url = f"{WEBAPP_URL}/auth/telegram-callback?token={token}&action=register"
    
    send_telegram_message_with_button(
        chat_id,
        f"✅ Номер <code>{phone_number}</code> получен!\n\n"
        f"Нажмите кнопку ниже, чтобы продолжить регистрацию на сайте:",
        "📝 Продолжить регистрацию",
        webapp_url
    )
    
    return jsonify({'ok': True})


def handle_start_command(chat_id: int, text: str):
    """Обработка команды /start"""
    parts = text.split()
    
    if len(parts) > 1:
        param = parts[1]  # /start <param>
        
        # Проверяем, это токен регистрации или восстановления пароля
        if param.startswith('reg_'):
            # Старый формат - оставляем для совместимости
            phone = param[4:]  # reg_+79991234567
            chat_ids[phone] = chat_id
            send_telegram_message(
                chat_id,
                f"✅ Номер <code>{phone}</code> привязан!\n\n"
                f"Теперь вы можете использовать этот номер для входа на сайт."
            )
        elif param.startswith('reset_'):
            # Токен сброса пароля
            token = param[6:]
            handle_password_reset_token(chat_id, token)
        else:
            # Отправляем кнопку для передачи контакта
            send_contact_request_button(chat_id)
    else:
        # Отправляем кнопку для передачи контакта
        send_contact_request_button(chat_id)
    
    return jsonify({'ok': True})


def send_contact_request_button(chat_id: int):
    """Отправляет кнопку запроса контакта"""
    reply_markup = {
        'keyboard': [[{
            'text': '📱 Передать номер телефона',
            'request_contact': True
        }]],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    
    send_telegram_message(
        chat_id,
        "👋 Привет! Я бот для авторизации на сайте <b>Понятная Еда</b>.\n\n"
        "Чтобы зарегистрироваться или восстановить пароль, "
        "нажмите кнопку ниже для передачи номера телефона:",
        reply_markup
    )


def handle_callback_query(callback_query: dict):
    """Обработка callback запросов от inline кнопок"""
    # Пока не используем, но может пригодиться
    return jsonify({'ok': True})


def handle_password_reset_token(chat_id: int, token: str):
    """Обработка токена сброса пароля"""
    cleanup_expired_tokens()
    
    # Ищем токен в базе
    reset_data = None
    for t, data in password_reset_tokens.items():
        if t == token:
            reset_data = data
            break
    
    if not reset_data or reset_data['expires'] < datetime.now():
        send_telegram_message(
            chat_id,
            "❌ Ссылка для восстановления пароля устарела или недействительна.\n"
            "Запросите новую ссылку на сайте."
        )
        return jsonify({'ok': True})
    
    # Обновляем chat_id если нужно
    password_reset_tokens[token]['chat_id'] = chat_id
    chat_ids[reset_data['phone']] = chat_id
    
    # Отправляем код подтверждения
    code = reset_data['code']
    send_telegram_message(
        chat_id,
        f"🔐 <b>Восстановление пароля</b>\n\n"
        f"Ваш код для сброса пароля: <code>{code}</code>\n\n"
        f"⏳ Код действителен 10 минут.\n"
        f"Введите этот код на сайте, чтобы установить новый пароль."
    )


# ==================== API Endpoints для фронтенда ====================

@app.route('/api/initiate-registration', methods=['POST'])
def initiate_registration():
    """
    Инициирует процесс регистрации через Telegram.
    Возвращает ссылку для открытия бота.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    phone = data.get('phone', '').strip()
    
    if not phone:
        return jsonify({'error': 'Phone is required'}), 400
    
    # Нормализуем номер
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    phone = '+' + digits
    
    # Проверяем, не зарегистрирован ли уже
    if phone in chat_ids:
        return jsonify({
            'error': 'Phone already registered',
            'message': 'Этот номер уже привязан к Telegram'
        }), 409
    
    # Генерируем токен для регистрации
    token = generate_token()
    registration_tokens[token] = {
        'phone': phone,
        'chat_id': None,
        'expires': datetime.now() + timedelta(minutes=30)
    }
    
    # Формируем ссылку на бота
    bot_username = get_bot_username()
    if not bot_username:
        return jsonify({'error': 'Bot username not available'}), 500
    
    telegram_link = f"https://t.me/{bot_username}?start={token}"
    
    return jsonify({
        'success': True,
        'telegram_link': telegram_link,
        'token': token,
        'expires_in': 1800  # 30 минут в секундах
    })


@app.route('/api/check-registration/<token>', methods=['GET'])
def check_registration(token: str):
    """
    Проверяет статус регистрации по токену.
    Возвращает phone и chat_id если пользователь передал контакт.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    cleanup_expired_tokens()
    
    data = registration_tokens.get(token)
    if not data:
        return jsonify({
            'error': 'Token not found or expired',
            'status': 'expired'
        }), 404
    
    if data['expires'] < datetime.now():
        del registration_tokens[token]
        return jsonify({
            'error': 'Token expired',
            'status': 'expired'
        }), 410
    
    # Если chat_id есть - значит пользователь передал контакт
    if data.get('chat_id'):
        return jsonify({
            'success': True,
            'status': 'completed',
            'phone': data['phone'],
            'chat_id': data['chat_id'],
            'first_name': data.get('first_name', '')
        })
    
    return jsonify({
        'success': True,
        'status': 'pending',
        'phone': data['phone']
    })


@app.route('/api/initiate-password-reset', methods=['POST'])
def initiate_password_reset():
    """
    Инициирует восстановление пароля.
    Принимает phone, возвращает ссылку на бота.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    phone = data.get('phone', '').strip()
    
    if not phone:
        return jsonify({'error': 'Phone is required'}), 400
    
    # Нормализуем номер
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    phone = '+' + digits
    
    # Проверяем, есть ли chat_id для этого номера
    chat_id = chat_ids.get(phone)
    
    # Генерируем код и токен
    code = ''.join(secrets.choice('0123456789') for _ in range(6))
    token = generate_token()
    
    password_reset_tokens[token] = {
        'phone': phone,
        'chat_id': chat_id,
        'code': code,
        'expires': datetime.now() + timedelta(minutes=10)
    }
    
    bot_username = get_bot_username()
    if not bot_username:
        return jsonify({'error': 'Bot username not available'}), 500
    
    # Если chat_id известен - отправляем код сразу
    if chat_id:
        send_telegram_message(
            chat_id,
            f"🔐 <b>Восстановление пароля</b>\n\n"
            f"Ваш код для сброса пароля: <code>{code}</code>\n\n"
            f"⏳ Код действителен 10 минут."
        )
        telegram_link = None  # Не нужна ссылка, код уже отправлен
    else:
        # Формируем ссылку для открытия бота
        telegram_link = f"https://t.me/{bot_username}?start=reset_{token}"
    
    return jsonify({
        'success': True,
        'telegram_link': telegram_link,
        'token': token,
        'code_sent_directly': chat_id is not None,
        'expires_in': 600  # 10 минут
    })


@app.route('/api/verify-reset-code', methods=['POST'])
def verify_reset_code():
    """
    Проверяет код восстановления пароля.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    token = data.get('token')
    code = data.get('code')
    
    if not token or not code:
        return jsonify({'error': 'Token and code are required'}), 400
    
    cleanup_expired_tokens()
    
    reset_data = password_reset_tokens.get(token)
    if not reset_data:
        return jsonify({'error': 'Invalid or expired token'}), 404
    
    if reset_data['expires'] < datetime.now():
        del password_reset_tokens[token]
        return jsonify({'error': 'Token expired'}), 410
    
    if reset_data['code'] != code:
        return jsonify({'error': 'Invalid code'}), 400
    
    # Код верный - возвращаем временный токен для смены пароля
    temp_token = generate_token()
    password_reset_tokens[temp_token] = {
        'phone': reset_data['phone'],
        'chat_id': reset_data['chat_id'],
        'verified': True,
        'expires': datetime.now() + timedelta(minutes=10)
    }
    
    # Удаляем старый токен
    if token in password_reset_tokens:
        del password_reset_tokens[token]
    
    return jsonify({
        'success': True,
        'temp_token': temp_token,
        'phone': reset_data['phone']
    })


@app.route('/send-code', methods=['POST'])
def send_code():
    """
    API для отправки кода подтверждения (устаревший, для совместимости).
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    phone = data.get('phone')
    code = data.get('code')
    
    if not phone or not code:
        return jsonify({'error': 'Phone and code are required'}), 400
    
    chat_id = chat_ids.get(phone)
    if not chat_id:
        return jsonify({
            'error': 'Phone not linked to Telegram',
            'message': 'Пользователь не привязал номер к Telegram.'
        }), 404
    
    message = (
        f"🔐 <b>Код подтверждения</b>\n\n"
        f"Ваш код: <code>{code}</code>\n\n"
        f"⏳ Код действителен 10 минут."
    )
    
    result = send_telegram_message(chat_id, message)
    
    if result['success']:
        return jsonify({'success': True, 'message': 'Code sent successfully'})
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


def get_bot_username() -> str:
    """Получает username бота через Bot API"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                return data['result'].get('username', '')
    except Exception as e:
        print(f"Error getting bot username: {e}")
    return ''


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
