#!/usr/bin/env python3
"""
Добавляет/удаляет RKN блокировку доменов в конфиге 3x-ui через базу данных
Usage:
    python3 update-3xui-rkn-domains.py enable   - Добавить RKN правила
    python3 update-3xui-rkn-domains.py disable  - Удалить RKN правила

IMPORTANT: x-ui должен быть ОСТАНОВЛЕН перед запуском этого скрипта!
"""
import json
import sqlite3
import subprocess
import sys
import time

DB_PATH = '/etc/x-ui/x-ui.db'

# Тестовые домены
TEST_DOMAINS = ['facebook.com', 'tiktok.com', 'x.com']

def get_blocked_domains(use_test=True):
    if use_test:
        return TEST_DOMAINS
    return TEST_DOMAINS

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update-3xui-rkn-domains.py [enable|disable]")
        return 1
    
    action = sys.argv[1]
    
    # СНАЧАЛА останавливаем x-ui чтобы освободить базу
    print(f"Stopping x-ui...")
    try:
        subprocess.run(['systemctl', 'stop', 'x-ui'], check=True, timeout=30)
        print(f"x-ui stopped")
        time.sleep(2)  # Ждём пока x-ui полностью освободит базу
    except Exception as e:
        print(f"Warning: Failed to stop x-ui: {e}")
    
    # Читаем конфиг из базы
    conn = sqlite3.connect(DB_PATH, timeout=30)
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = cursor.fetchone()
    
    if not row:
        print("xrayTemplateConfig not found in database")
        conn.close()
        return 1
    
    try:
        config = json.loads(row[0])
    except Exception as e:
        print(f"Error parsing config: {e}")
        conn.close()
        return 1
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    if action == 'enable':
        domains = get_blocked_domains()
        
        # Удаляем старые RKN правила
        new_rules = []
        for rule in rules:
            domain_list = rule.get('domain', [])
            is_rkn = ('facebook.com' in domain_list) or (len(domain_list) > 10 and rule.get('outboundTag') == 'blocked')
            if is_rkn:
                continue
            new_rules.append(rule)
        rules = new_rules
        
        # Находим позицию для вставки
        insert_index = 0
        for i, rule in enumerate(rules):
            if 'inboundTag' not in rule and 'outboundTag' in rule:
                insert_index = i
                break
        
        # Добавляем правило
        rkn_rule = {
            'type': 'field',
            'domain': domains,
            'network': 'TCP,UDP',
            'outboundTag': 'blocked'
        }
        rules.insert(insert_index, rkn_rule)
        print(f"Added RKN rule with {len(domains)} domains")
        print(f"About to save...")
    
    elif action == 'disable':
        # Удаляем RKN правила
        new_rules = []
        removed = 0
        for rule in rules:
            domain_list = rule.get('domain', [])
            is_rkn = ('facebook.com' in domain_list) or (len(domain_list) > 10 and rule.get('outboundTag') == 'blocked')
            if is_rkn:
                removed += 1
                continue
            new_rules.append(rule)
        rules = new_rules
        print(f"Removed {removed} RKN rules")
    
    routing['rules'] = rules
    config['routing'] = routing
    
    # Сохраняем в базу
    try:
        cursor.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(config),)
        )
        conn.commit()
        print("Config saved")
    except Exception as e:
        print(f"Error saving: {e}")
        conn.close()
        return 1
    finally:
        conn.close()
    
    # Запускаем x-ui
    print(f"Starting x-ui...")
    try:
        subprocess.run(['systemctl', 'start', 'x-ui'], check=True, timeout=30)
        print(f"x-ui started")
        time.sleep(5)  # Ждём пока x-ui полностью запустится
    except Exception as e:
        print(f"Error starting x-ui: {e}")
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
