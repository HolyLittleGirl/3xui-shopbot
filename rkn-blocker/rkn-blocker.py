#!/usr/bin/env python3
"""
RKN Blocker - Блокировка запрещённых ресурсов РФ через 3x-ui routing

Использует IP списки из antifilter.download для создания правил в 3x-ui routing.

Usage:
    python3 rkn-blocker.py enable   - Включить блокировку
    python3 rkn-blocker.py disable  - Выключить блокировку
    python3 rkn-blocker.py update   - Обновить списки
"""
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

DB_PATH = '/etc/x-ui/x-ui.db'
IP_LIST_URL = 'https://antifilter.download/list/allyouneed.lst'
MAX_IPS_PER_RULE = 500  # Лимит Xray для IP правил


def log(message: str):
    print(message)


def run_command(cmd: list, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def fetch_iplist() -> list:
    """Скачать список IP"""
    log(f"Fetching IP list from {IP_LIST_URL}...")
    try:
        result = run_command(['curl', '-sL', '--connect-timeout', '30', IP_LIST_URL], timeout=120)
        if result.returncode != 0:
            log(f"Failed to fetch: {result.stderr}")
            return []
        
        ips = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
        log(f"Fetched {len(ips)} IPs")
        return ips
    except Exception as e:
        log(f"Error: {e}")
        return []


def get_outbound_ips() -> list:
    """Получить IP аутбаундов для whitelist"""
    outbound_ips = []
    
    try:
        # Читаем конфиг
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return []
        
        config = json.loads(row[0])
        for outbound in config.get('outbounds', []):
            if outbound.get('protocol') == 'vless':
                address = outbound.get('settings', {}).get('address', '')
                if address and not address.startswith('geo:'):
                    # Проверяем не IP ли это
                    import re
                    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', address):
                        ip = address.split('/')[0]
                        if ip not in outbound_ips:
                            outbound_ips.append(ip)
                    else:
                        # Резолвим hostname
                        try:
                            result = run_command(['getent', 'hosts', address], timeout=5)
                            if result.returncode == 0:
                                for line in result.stdout.strip().split('\n'):
                                    ip = line.split()[0]
                                    if '.' in ip and ip not in outbound_ips:
                                        outbound_ips.append(ip)
                        except:
                            pass
    except Exception as e:
        log(f"Error getting outbound IPs: {e}")
    
    log(f"Found {len(outbound_ips)} outbound IPs: {outbound_ips}")
    return outbound_ips


def split_ips(ips: list, max_per_rule: int = MAX_IPS_PER_RULE) -> list:
    """Разбить IP на чанки"""
    chunks = []
    for i in range(0, len(ips), max_per_rule):
        chunks.append(ips[i:i + max_per_rule])
    return chunks


def enable_blocking(ips: list) -> dict:
    """Включить блокировку через 3x-ui routing"""
    log("=== Enabling RKN blocking via 3x-ui routing ===")
    
    # Получаем whitelist IP
    whitelist_ips = get_outbound_ips()
    
    # Исключаем whitelist из блокировки
    blocked_ips = [ip for ip in ips if ip not in whitelist_ips]
    log(f"Blocked IPs: {len(blocked_ips)} (excluded {len(whitelist_ips)} whitelist IPs)")
    
    # Разбиваем на чанки
    chunks = split_ips(blocked_ips)
    log(f"Split into {len(chunks)} rules ({MAX_IPS_PER_RULE} IPs each)")
    
    # Читаем конфиг
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = cursor.fetchone()
    
    if not row:
        log("xrayTemplateConfig not found")
        return {"success": False, "error": "Config not found"}
    
    try:
        config = json.loads(row[0])
    except Exception as e:
        log(f"Error parsing config: {e}")
        return {"success": False, "error": str(e)}
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    # Удаляем старые RKN правила
    new_rules = []
    old_rkn_count = 0
    for rule in rules:
        ip_list = rule.get('ip', [])
        # RKN правило: много IP и outboundTag: blocked
        is_rkn = len(ip_list) > 100 and rule.get('outboundTag') == 'blocked'
        if is_rkn:
            old_rkn_count += 1
            continue
        new_rules.append(rule)
    rules = new_rules
    log(f"Removed {old_rkn_count} old RKN rules")
    
    # Находим позицию для вставки (ПЕРЕД outbound правилами)
    insert_index = 0
    for i, rule in enumerate(rules):
        if 'inboundTag' not in rule and 'outboundTag' in rule:
            insert_index = i
            break
    log(f"Insert at index {insert_index}")
    
    # Добавляем правила
    for i, chunk in enumerate(chunks):
        rkn_rule = {
            'type': 'field',
            'ip': chunk,
            'outboundTag': 'blocked'
        }
        rules.insert(insert_index + i, rkn_rule)
        log(f"Added RKN IP rule {i+1}/{len(chunks)} with {len(chunk)} IPs")
    
    log(f"Total: {len(chunks)} RKN rules, {len(blocked_ips)} IPs blocked")
    
    routing['rules'] = rules
    config['routing'] = routing
    
    # Сохраняем
    try:
        cursor.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(config),)
        )
        conn.commit()
        log("Config saved to database")
    except Exception as e:
        log(f"Error saving config: {e}")
        return {"success": False, "error": str(e)}
    finally:
        conn.close()
    
    # Перезагружаем x-ui
    try:
        log("Stopping x-ui...")
        subprocess.run(['systemctl', 'stop', 'x-ui'], check=True, timeout=30)
        log("x-ui stopped")
        time.sleep(2)
        log("Starting x-ui...")
        subprocess.run(['systemctl', 'start', 'x-ui'], check=True, timeout=30)
        log("x-ui started")
    except Exception as e:
        log(f"Error restarting x-ui: {e}")
        return {"success": False, "error": str(e)}
    
    return {"success": True, "blocked_count": len(blocked_ips)}


def disable_blocking() -> dict:
    """Выключить блокировку"""
    log("=== Disabling RKN blocking ===")
    
    # Читаем конфиг
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = cursor.fetchone()
    
    if not row:
        log("xrayTemplateConfig not found")
        return {"success": False, "error": "Config not found"}
    
    try:
        config = json.loads(row[0])
    except Exception as e:
        log(f"Error parsing config: {e}")
        return {"success": False, "error": str(e)}
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    # Удаляем RKN правила
    new_rules = []
    removed_count = 0
    for rule in rules:
        ip_list = rule.get('ip', [])
        is_rkn = len(ip_list) > 100 and rule.get('outboundTag') == 'blocked'
        if is_rkn:
            removed_count += 1
            continue
        new_rules.append(rule)
    rules = new_rules
    
    log(f"Removed {removed_count} RKN IP rules")
    
    routing['rules'] = rules
    config['routing'] = routing
    
    # Сохраняем
    try:
        cursor.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(config),)
        )
        conn.commit()
        log("Config saved to database")
    except Exception as e:
        log(f"Error saving config: {e}")
        return {"success": False, "error": str(e)}
    finally:
        conn.close()
    
    # Перезагружаем x-ui
    try:
        log("Stopping x-ui...")
        subprocess.run(['systemctl', 'stop', 'x-ui'], check=True, timeout=30)
        log("x-ui stopped")
        time.sleep(2)
        log("Starting x-ui...")
        subprocess.run(['systemctl', 'start', 'x-ui'], check=True, timeout=30)
        log("x-ui started")
    except Exception as e:
        log(f"Error restarting x-ui: {e}")
        return {"success": False, "error": str(e)}
    
    log("Blocking disabled")
    return {"success": True}


def update_blocklist() -> dict:
    """Обновить списки (выключить + включить)"""
    log("=== Updating blocklist ===")
    disable_blocking()
    time.sleep(5)
    ips = fetch_iplist()
    if not ips:
        return {"success": False, "error": "Failed to fetch IP list"}
    return enable_blocking(ips)


def get_status() -> dict:
    """Получить статус"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {"enabled": False, "blocked_count": 0}
        
        config = json.loads(row[0])
        rules = config.get('routing', {}).get('rules', [])
        
        # Считаем RKN правила
        rkn_rules = [r for r in rules if len(r.get('ip', [])) > 100 and r.get('outboundTag') == 'blocked']
        blocked_count = sum(len(r.get('ip', [])) for r in rkn_rules)
        
        enabled = len(rkn_rules) > 0
        
        return {
            "enabled": enabled,
            "blocked_count": blocked_count,
            "rkn_rules": len(rkn_rules)
        }
    except Exception as e:
        return {"enabled": False, "blocked_count": 0, "error": str(e)}


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 rkn-blocker.py [enable|disable|update|status]")
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == 'enable':
        ips = fetch_iplist()
        if not ips:
            print(json.dumps({"success": False, "error": "Failed to fetch IP list"}))
            sys.exit(1)
        result = enable_blocking(ips)
    elif action == 'disable':
        result = disable_blocking()
    elif action == 'update':
        ips = fetch_iplist()
        if not ips:
            print(json.dumps({"success": False, "error": "Failed to fetch IP list"}))
            sys.exit(1)
        result = update_blocklist()
    elif action == 'status':
        result = get_status()
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
    
    print(json.dumps(result))
    sys.exit(0 if result.get('success') else 1)
