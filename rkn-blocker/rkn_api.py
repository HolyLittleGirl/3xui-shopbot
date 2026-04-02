#!/usr/bin/env python3
"""
RKN Blocker HTTP API
Сервер для управления блокировщиком через HTTP запросы

Usage:
    python3 rkn_api.py [--port PORT] [--token TOKEN]
"""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify
from functools import wraps

# Конфигурация
INSTALL_DIR = Path("/opt/rkn-blocker")
STATE_FILE = INSTALL_DIR / "state.json"
ENV_FILE = Path("/etc/rkn-blocker.env")
BLOCK_IPS_SCRIPT = INSTALL_DIR / "rkn-blocker.py"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("/var/log/rkn-blocker/api.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def load_token() -> str:
    """Загрузить API токен из файла окружения."""
    if ENV_FILE.exists():
        try:
            with open(ENV_FILE, 'r') as f:
                for line in f:
                    if line.startswith("RKN_API_TOKEN="):
                        return line.split("=", 1)[1].strip()
        except Exception as e:
            logger.error(f"Ошибка чтения {ENV_FILE}: {e}")
    
    # Fallback на дефолтный токен (только для первого запуска!)
    return "change-me-immediately"


def check_auth(f):
    """Декоратор проверки авторизации."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = load_token()

        # Проверка токена из заголовка (приоритет)
        auth_token = request.headers.get("X-RKN-Token")
        
        # Или из JSON body (для POST запросов)
        if not auth_token and request.is_json:
            auth_token = request.json.get("token")

        if not auth_token or auth_token != token:
            logger.warning(f"Неавторизованный запрос от {request.remote_addr}")
            return jsonify({"error": "Unauthorized", "success": False}), 401

        return f(*args, **kwargs)

    return decorated


def run_blocker_command(action: str) -> dict:
    """Выполнить команду блокировщика."""
    try:
        result = subprocess.run(
            ["python3", str(BLOCK_IPS_SCRIPT), action],
            capture_output=True,
            text=True,
            timeout=300  # 5 минут для загрузки IP списков
        )

        # Парсим вывод (даже если returncode != 0)
        try:
            return json.loads(result.stdout)
        except:
            if result.returncode == 0:
                return {"success": True}
            else:
                logger.error(f"Ошибка выполнения: {result.stderr}")
                return {"success": False, "error": result.stderr}

    except subprocess.TimeoutExpired:
        logger.error("Таймаут выполнения команды")
        return {"success": False, "error": "Timeout"}
    except Exception as e:
        logger.error(f"Исключение при выполнении: {e}")
        return {"success": False, "error": str(e)}


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


@app.route("/status", methods=["GET"])
@check_auth
def status():
    """Получить статус блокировщика."""
    logger.info("Запрос статуса")
    result = run_blocker_command("status")
    return jsonify(result)


@app.route("/enable", methods=["POST"])
@check_auth
def enable():
    """Включить блокировку."""
    logger.info("Запрос на включение блокировки")
    result = run_blocker_command("enable")
    
    if result.get("success"):
        logger.info(f"Блокировка включена: {result.get('blocked_count')} IP")
    
    return jsonify(result)


@app.route("/disable", methods=["POST"])
@check_auth
def disable():
    """Выключить блокировку."""
    logger.info("Запрос на выключение блокировки")
    result = run_blocker_command("disable")
    
    if result.get("success"):
        logger.info("Блокировка выключена")
    
    return jsonify(result)


@app.route("/update", methods=["POST"])
@check_auth
def update():
    """Обновить список блокировки."""
    logger.info("Запрос на обновление блоклиста")
    result = run_blocker_command("update")
    
    if result.get("success"):
        logger.info(f"Блоклист обновлён: {result.get('blocked_count')} IP")
    
    return jsonify(result)


@app.route("/toggle", methods=["POST"])
@check_auth
def toggle():
    """Переключить состояние блокировки."""
    current = run_blocker_command("status")
    
    if current.get("enabled"):
        result = run_blocker_command("disable")
        result["action"] = "disabled"
    else:
        result = run_blocker_command("enable")
        result["action"] = "enabled"
    
    return jsonify(result)


@app.route("/token", methods=["GET"])
@check_auth
def get_token_info():
    """Получить информацию о токене (без самого токена)."""
    token = load_token()
    masked = token[:4] + "..." + token[-4:] if len(token) > 8 else "****"
    return jsonify({
        "token_masked": masked,
        "token_length": len(token)
    })


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found", "success": False}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error", "success": False}), 500


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="RKN Blocker API")
    parser.add_argument("--port", type=int, default=8765, help="Порт для API сервера")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Хост для API сервера")
    parser.add_argument("--debug", action="store_true", help="Режим отладки")
    
    args = parser.parse_args()
    
    logger.info(f"=== Запуск RKN API сервера на {args.host}:{args.port} ===")
    
    # Запускаем Flask
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True
    )
