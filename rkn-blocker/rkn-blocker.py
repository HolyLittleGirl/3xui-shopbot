#!/usr/bin/env python3
"""
RKN Blocker - Блокировка запрещённых ресурсов РФ через 3x-ui routing domain rules

Использует локальный файл доменов /opt/rkn-blocker/blocked_domains.json

Usage:
    python3 rkn-blocker.py enable   - Включить блокировку
    python3 rkn-blocker.py disable  - Выключить блокировку
    python3 rkn-blocker.py status   - Статус блокировки
"""
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

DB_PATH = '/etc/x-ui/x-ui.db'
DOMAINS_FILE = '/opt/rkn-blocker/blocked_domains.json'


def log(message: str):
    print(message)


def run_command(cmd: list, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def load_domains() -> list:
    """Загрузить домены из локального файла"""
    if not subprocess.run(['test', '-f', DOMAINS_FILE]).returncode == 0:
        log(f"Domains file not found: {DOMAINS_FILE}")
        return []
    
    try:
        with open(DOMAINS_FILE, 'r') as f:
            data = json.load(f)
        
        domains = []
        for rule in data.get('rules', []):
            for domain in rule.get('domain', []):
                if not domain.startswith('regexp:') and domain not in domains:
                    domains.append(domain)
        
        log(f"Loaded {len(domains)} domains from {DOMAINS_FILE}")
        return domains
    
    except Exception as e:
        log(f"Error loading domains: {e}")
        return []


def enable_blocking(domains: list) -> dict:
    """Включить блокировку через 3x-ui routing"""
    log("=== Enabling RKN domain blocking via 3x-ui routing ===")
    
    if not domains:
        return {"success": False, "error": "No domains to block"}
    
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
        domain_list = rule.get('domain', [])
        # RKN правило: домены и outboundTag: blocked
        is_rkn = len(domain_list) > 5 and rule.get('outboundTag') == 'blocked'
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
    
    # Добавляем правило
    rkn_rule = {
        'type': 'field',
        'domain': domains,
        'network': 'TCP,UDP',
        'outboundTag': 'blocked'
    }
    rules.insert(insert_index, rkn_rule)
    log(f"Added RKN domain rule with {len(domains)} domains")
    
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
    
    return {"success": True, "blocked_count": len(domains)}


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
        domain_list = rule.get('domain', [])
        is_rkn = len(domain_list) > 5 and rule.get('outboundTag') == 'blocked'
        if is_rkn:
            removed_count += 1
            continue
        new_rules.append(rule)
    rules = new_rules
    
    log(f"Removed {removed_count} RKN domain rules")
    
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
        rkn_rules = [r for r in rules if len(r.get('domain', [])) > 5 and r.get('outboundTag') == 'blocked']
        blocked_count = sum(len(r.get('domain', [])) for r in rkn_rules)
        
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
        print("Usage: python3 rkn-blocker.py [enable|disable|status]")
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == 'enable':
        domains = load_domains()
        if not domains:
            print(json.dumps({"success": False, "error": "No domains found"}))
            sys.exit(1)
        result = enable_blocking(domains)
    elif action == 'disable':
        result = disable_blocking()
    elif action == 'status':
        result = get_status()
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
    
    print(json.dumps(result))
    sys.exit(0 if result.get('success') else 1)
