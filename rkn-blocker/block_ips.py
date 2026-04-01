#!/usr/bin/env python3
"""
RKN IP Blocker - Блокировка запрещённых ресурсов РФ
Загружает списки IP с antifilter.download и блокирует через ipset/iptables

Usage:
    python3 block_ips.py [--enable|--disable|--status|--update]
"""

import subprocess
import requests
import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# Конфигурация
INSTALL_DIR = Path("/opt/rkn-blocker")
LOG_DIR = Path("/var/log/rkn-blocker")
STATE_FILE = INSTALL_DIR / "state.json"
IPSET_NAME = "rkn_blocked"
BLOCKLIST_URL = "https://antifilter.download/list/allyouneed.lst"
TIMEOUT = 30

# Настройка логирования
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "block_ips.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def load_state() -> dict:
    """Загрузить состояние блокировщика."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка чтения state.json: {e}")
    return {
        "enabled": False,
        "last_update": None,
        "blocked_count": 0
    }


def save_state(state: dict):
    """Сохранить состояние блокировщика."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Ошибка записи state.json: {e}")


def run_command(cmd: list, check: bool = False) -> subprocess.CompletedProcess:
    """Выполнить системную команду."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Команда вернула ошибку: {e.stderr}")
        return e


def ensure_ipset() -> bool:
    """Создать ipset если не существует."""
    # Проверка существования ipset
    result = run_command(["ipset", "list", "-t", IPSET_NAME])
    
    if result.returncode == 0:
        # Ipset существует - очищаем
        logger.info(f"ipset {IPSET_NAME} уже существует, очищаю...")
        run_command(["ipset", "flush", IPSET_NAME])
        return True
    
    # Создание нового ipset
    logger.info(f"Создаю ipset {IPSET_NAME}...")
    result = run_command([
        "ipset", "create", IPSET_NAME, "hash:net",
        "maxelem", "2097152",
        "timeout", "0"
    ])
    
    if result.returncode != 0:
        logger.error(f"Не удалось создать ipset: {result.stderr}")
        return False
    
    logger.info(f"ipset {IPSET_NAME} создан успешно")
    return True


# Xray VLESS порты (больше не используются - используем только OUTPUT chain)
# XRAY_PORTS = [5443, 6443, 7443, 8443]

# Whitelist для аутбаундов (заполняется автоматически)
WHITELIST_IPSET = "rkn_whitelist"
XUI_DB_PATH = "/etc/x-ui/x-ui.db"


def get_outbound_ips() -> list:
    """Получить IP аутбаундов из базы 3x-ui."""
    import subprocess
    import re
    
    outbound_ips = []
    
    try:
        # Извлекаем target из realitySettings
        result = subprocess.run(
            ["sqlite3", XUI_DB_PATH, "SELECT stream_settings FROM inbounds WHERE stream_settings LIKE '%target%';"],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                # Извлекаем target из JSON
                match = re.search(r'"target":"([^"]+)"', line)
                if match:
                    target = match.group(1)
                    hostname = target.split(':')[0]
                    
                    # Резолвим hostname в IP
                    try:
                        ip_result = subprocess.run(
                            ["getent", "hosts", hostname],
                            capture_output=True, text=True, timeout=5
                        )
                        if ip_result.returncode == 0:
                            for ip_line in ip_result.stdout.strip().split('\n'):
                                ip = ip_line.split()[0]
                                # Только IPv4
                                if '.' in ip and ':' not in ip:
                                    outbound_ips.append(ip)
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"Failed to get outbound IPs: {e}")
    
    return outbound_ips


def ensure_whitelist() -> bool:
    """Создать whitelist и добавить аутбаунды."""
    # Создаём ipset
    result = run_command(["ipset", "create", WHITELIST_IPSET, "hash:net", "maxelem", "1024"])
    if result.returncode != 0 and "already exists" not in result.stderr:
        logger.warning(f"Failed to create whitelist ipset: {result.stderr}")
    
    # Получаем аутбаунды
    outbound_ips = get_outbound_ips()
    logger.info(f"Found {len(outbound_ips)} outbound IPs: {outbound_ips}")
    
    # Добавляем в whitelist
    for ip in outbound_ips:
        run_command(["ipset", "add", WHITELIST_IPSET, ip])
    
    # Добавляем Apple range (для iCloud)
    run_command(["ipset", "add", WHITELIST_IPSET, "17.0.0.0/8"])
    
    # Добавляем правило в OUTPUT chain (ПЕРЕД правилом блокировки)
    result = run_command([
        "iptables", "-C", "OUTPUT",
        "-m", "set", "--match-set", WHITELIST_IPSET, "dst",
        "-j", "ACCEPT"
    ])
    
    if result.returncode != 0:
        # Находим позицию правила блокировки
        result = run_command(["iptables", "-L", "OUTPUT", "-n", "--line-numbers"])
        
        block_line = None
        for line in result.stdout.split('\n'):
            if 'rkn_blocked' in line and 'DROP' in line:
                block_line = line.split()[0]
                break
        
        if block_line:
            run_command([
                "iptables", "-I", "OUTPUT", block_line,
                "-m", "set", "--match-set", WHITELIST_IPSET, "dst",
                "-j", "ACCEPT"
            ])
        else:
            run_command([
                "iptables", "-I", "OUTPUT", "1",
                "-m", "set", "--match-set", WHITELIST_IPSET, "dst",
                "-j", "ACCEPT"
            ])
        
        logger.info("Whitelist rule added to OUTPUT chain (before RKN rules)")
    
    return True


def ensure_iptables_rule() -> bool:
    """Добавить правило iptables если отсутствует.
    
    Используем OUTPUT chain с conntrack NEW.
    """
    # OUTPUT chain - для общего трафика
    result = run_command([
        "iptables", "-C", "OUTPUT",
        "-m", "conntrack", "--ctstate", "NEW",
        "-m", "set", "--match-set", IPSET_NAME, "dst",
        "-j", "DROP"
    ])

    if result.returncode != 0:
        result = run_command([
            "iptables", "-I", "OUTPUT", "1",
            "-m", "conntrack", "--ctstate", "NEW",
            "-m", "set", "--match-set", IPSET_NAME, "dst",
            "-j", "DROP"
        ])
        if result.returncode == 0:
            logger.info("Правило iptables OUTPUT (conntrack NEW) добавлено успешно")
        else:
            logger.error(f"Не удалось добавить правило OUTPUT: {result.stderr}")
            return False
    else:
        logger.info("Правило iptables OUTPUT уже существует")

    return True


def remove_iptables_rule() -> bool:
    """Удалить правила iptables из OUTPUT."""
    # Удаляем правило OUTPUT с conntrack
    result = run_command([
        "iptables", "-D", "OUTPUT",
        "-m", "conntrack", "--ctstate", "NEW",
        "-m", "set", "--match-set", IPSET_NAME, "dst",
        "-j", "DROP"
    ])
    if result.returncode == 0:
        logger.info("Правило iptables OUTPUT удалено")
    else:
        logger.debug(f"Правило OUTPUT не найдено: {result.stderr}")
    
    # Удаляем whitelist правило из OUTPUT
    run_command([
        "iptables", "-D", "OUTPUT",
        "-m", "set", "--match-set", WHITELIST_IPSET, "dst",
        "-j", "ACCEPT"
    ])
    logger.debug("Whitelist rule removed from OUTPUT")

    return True


def destroy_ipset() -> bool:
    """Удалить ipset."""
    result = run_command(["ipset", "destroy", IPSET_NAME])
    
    if result.returncode != 0:
        logger.warning(f"Не удалось удалить ipset: {result.stderr}")
        return False
    
    logger.info("ipset удалён")
    return True


def fetch_blocklist() -> list:
    """Загрузить список IP для блокировки."""
    try:
        logger.info(f"Загружаю блоклист с {BLOCKLIST_URL}...")
        response = requests.get(BLOCKLIST_URL, timeout=TIMEOUT)
        response.raise_for_status()
        
        # Парсим IP/подсети
        ips = []
        for line in response.text.strip().split('\n'):
            ip = line.strip()
            if ip and not ip.startswith('#'):
                ips.append(ip)
        
        logger.info(f"Загружено {len(ips)} IP адресов/подсетей")
        return ips
    
    except requests.RequestException as e:
        logger.error(f"Ошибка загрузки блоклиста: {e}")
        return []


def block_ips(ips: list) -> bool:
    """Добавить IP в ipset пакетно."""
    if not ips:
        logger.warning("Список IP пуст")
        return False
    
    # Используем директорию логов вместо /tmp
    temp_file = LOG_DIR / "ipset_restore.txt"
    
    try:
        # Формируем команды для ipset restore
        with open(temp_file, 'w') as f:
            f.write(f"flush {IPSET_NAME}\n")
            for ip in ips:
                f.write(f"add {IPSET_NAME} {ip} -exist\n")
        
        # Применяем пакетно через subprocess с stdin
        with open(temp_file, 'r') as f:
            result = subprocess.run(
                ["ipset", "restore"],
                stdin=f,
                capture_output=True,
                text=True
            )
        
        if result.returncode != 0:
            logger.error(f"Ошибка ipset restore: {result.stderr}")
            return False
        
        logger.info(f"Добавлено {len(ips)} IP адресов в ipset")
        return True
    
    except Exception as e:
        logger.error(f"Ошибка при добавлении IP: {e}")
        return False


def enable_blocking() -> dict:
    """Включить блокировку."""
    logger.info("=== Включение блокировки РКН ===")
    
    state = load_state()
    
    # Обновляем списки
    ips = fetch_blocklist()
    if not ips:
        return {"success": False, "error": "Не удалось загрузить список IP"}
    
    # Создаём ipset
    if not ensure_ipset():
        return {"success": False, "error": "Не удалось создать ipset"}
    
    # Добавляем IP
    if not block_ips(ips):
        return {"success": False, "error": "Не удалось добавить IP в ipset"}
    
    # Сначала добавляем правила блокировки
    if not ensure_iptables_rule():
        return {"success": False, "error": "Не удалось добавить правила iptables"}
    
    # ПОСЛЕ правил блокировки добавляем whitelist (чтобы whitelist был ПЕРЕД блокировкой)
    ensure_whitelist()
    
    # Сохраняем состояние
    state["enabled"] = True
    state["last_update"] = datetime.now().isoformat()
    state["blocked_count"] = len(ips)
    save_state(state)
    
    logger.info(f"Блокировка включена. Заблокировано {len(ips)} IP адресов")
    return {
        "success": True,
        "blocked_count": len(ips),
        "last_update": state["last_update"]
    }


def disable_blocking() -> dict:
    """Выключить блокировку."""
    logger.info("=== Выключение блокировки РКН ===")
    
    state = load_state()
    
    # Удаляем правило iptables
    remove_iptables_rule()
    
    # Удаляем ipset
    destroy_ipset()
    
    # Сохраняем состояние (но сохраняем last_update и blocked_count для истории)
    state["enabled"] = False
    save_state(state)
    
    logger.info("Блокировка выключена")
    return {"success": True}


def update_blocklist() -> dict:
    """Обновить список блокировки."""
    logger.info("=== Обновление блоклиста РКН ===")
    
    state = load_state()
    
    if not state.get("enabled"):
        logger.warning("Блокировка выключена, обновление пропущено")
        return {"success": False, "error": "Блокировка выключена"}
    
    # Загружаем новые списки
    ips = fetch_blocklist()
    if not ips:
        return {"success": False, "error": "Не удалось загрузить список IP"}
    
    # Обновляем ipset
    if not block_ips(ips):
        return {"success": False, "error": "Не удалось обновить ipset"}
    
    # Сохраняем состояние
    state["last_update"] = datetime.now().isoformat()
    state["blocked_count"] = len(ips)
    save_state(state)
    
    logger.info(f"Блоклист обновлён. Заблокировано {len(ips)} IP адресов")
    return {
        "success": True,
        "blocked_count": len(ips),
        "last_update": state["last_update"]
    }


def get_status() -> dict:
    """Получить статус блокировщика."""
    state = load_state()
    
    # Проверяем реальное состояние
    ipset_exists = run_command(["ipset", "list", "-t", IPSET_NAME]).returncode == 0
    iptables_exists = run_command([
        "iptables", "-C", "OUTPUT",
        "-m", "set", "--match-set", IPSET_NAME, "dst",
        "-j", "DROP"
    ]).returncode == 0
    
    # Получаем размер ipset
    blocked_count = state.get("blocked_count", 0)
    if ipset_exists:
        result = run_command(["ipset", "list", IPSET_NAME])
        if result.returncode == 0:
            # Считаем количество записей
            for line in result.stdout.split('\n'):
                if 'Number of entries:' in line:
                    try:
                        blocked_count = int(line.split(':')[1].strip())
                    except ValueError:
                        pass
    
    # enabled = true если blocked_count > 0 и ipset существует и iptables правило есть
    enabled = blocked_count > 0 and ipset_exists and iptables_exists
    
    return {
        "enabled": enabled,
        "blocked_count": blocked_count,
        "last_update": state.get("last_update"),
        "ipset_exists": ipset_exists,
        "iptables_exists": iptables_exists
    }


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="RKN IP Blocker")
    parser.add_argument(
        "action",
        nargs="?",
        choices=["enable", "disable", "status", "update", "run"],
        default="run",
        help="Действие: enable, disable, status, update, run (авто)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывод в формате JSON"
    )
    
    args = parser.parse_args()
    
    # Выполняем действие
    actions = {
        "enable": enable_blocking,
        "disable": disable_blocking,
        "status": get_status,
        "update": update_blocklist,
        "run": lambda: enable_blocking() if not load_state().get("enabled") else update_blocklist()
    }
    
    result = actions.get(args.action, get_status)()
    
    # Вывод результата
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result.get("success"):
            print(f"✅ {args.action.upper()}: {result}")
        else:
            print(f"❌ {args.action.upper()}: {result}")
    
    return 0 if result.get("success", True) else 1


if __name__ == "__main__":
    sys.exit(main())
