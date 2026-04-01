#!/usr/bin/env python3
"""
Добавляет/удаляет RKN блокировку доменов в конфиге 3x-ui через базу данных
Usage:
    python3 update-3xui-rkn-domains.py enable   - Добавить RKN правило
    python3 update-3xui-rkn-domains.py disable  - Удалить RKN правило
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

DB_PATH = '/etc/x-ui/x-ui.db'
DOMAINS_FILE = '/tmp/rkn_domains.json'

def get_blocked_domains():
    """Получить домены из файла"""
    try:
        with open(DOMAINS_FILE, 'r') as f:
            data = json.load(f)
        
        domains = []
        for rule in data.get('rules', []):
            for domain in rule.get('domain', []):
                # Добавляем префикс domain: для Xray
                domains.append(f"domain:{domain}")
        
        return domains[:1000]  # Лимит 1000 доменов (Xray имеет ограничения)
    except Exception as e:
        print(f"Error loading domains: {e}", file=sys.stderr)
        return []

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update-3xui-rkn-domains.py [enable|disable]", file=sys.stderr)
        return 1
    
    action = sys.argv[1]
    
    # Читаем конфиг из базы
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = cursor.fetchone()
    
    if not row:
        print("xrayTemplateConfig not found in database", file=sys.stderr)
        return 1
    
    try:
        config = json.loads(row[0])
    except Exception as e:
        print(f"Error parsing config: {e}", file=sys.stderr)
        return 1
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    if action == 'enable':
        domains = get_blocked_domains()
        if not domains:
            print("No domains found", file=sys.stderr)
            return 1
        
        print(f"Found {len(domains)} domains")
        
        # Проверяем есть ли уже правило RKN
        rkn_exists = False
        for rule in rules:
            if rule.get('_rkn_domains_blocker'):
                rkn_exists = True
                # Обновляем правило
                rule['domain'] = domains
                print("Updated existing RKN domain rule", file=sys.stderr)
                break
        
        if not rkn_exists:
            # Находим позицию для вставки (ПЕРЕД outbound правилами)
            insert_index = 0  # В самое начало
            for i, rule in enumerate(rules):
                if 'inboundTag' not in rule and 'outboundTag' in rule:
                    insert_index = i
                    break
            
            rkn_rule = {
                'type': 'field',
                'domain': domains,
                'network': 'TCP,UDP',
                'outboundTag': 'blocked',  # Блокируем доступ (blackhole)
                '_rkn_domains_blocker': True  # Маркер что это RKN правило
            }
            rules.insert(insert_index, rkn_rule)
            print(f"Added RKN domain rule at index {insert_index} with {len(domains)} domains", file=sys.stderr)
    
    elif action == 'disable':
        # Удаляем RKN правила (с маркером '_rkn_domains_blocker')
        original_count = len(rules)
        rules = [
            rule for rule in rules
            if not rule.get('_rkn_domains_blocker')
        ]
        removed_count = original_count - len(rules)
        print(f"Removed {removed_count} RKN domain rules", file=sys.stderr)
    
    routing['rules'] = rules
    config['routing'] = routing
    
    # Сохраняем в базу
    try:
        cursor.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(config),)
        )
        conn.commit()
        print("Config saved to database", file=sys.stderr)
        
        # Перезагружаем x-ui
        subprocess.run(['systemctl', 'restart', 'x-ui'], check=True)
        print("Xray restarted", file=sys.stderr)
        
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
