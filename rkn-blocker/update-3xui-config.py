#!/usr/bin/env python3
"""
Добавляет RKN блокировку в конфиг 3x-ui через базу данных
"""
import json
import sqlite3
import subprocess
import sys

DB_PATH = '/etc/x-ui/x-ui.db'

def get_blocked_ips():
    """Получить IP из ipset rkn_blocked"""
    result = subprocess.run(
        ['ipset', 'list', 'rkn_blocked'],
        capture_output=True, text=True, timeout=30
    )
    ips = []
    in_members = False
    for line in result.stdout.strip().split('\n'):
        if line.strip() == 'Members:':
            in_members = True
            continue
        if in_members and line.strip():
            ip = line.split()[0]
            if ip and not ip.startswith('#'):
                ips.append(ip)
    return ips

def main():
    blocked_ips = get_blocked_ips()
    if not blocked_ips:
        print("No blocked IPs found", file=sys.stderr)
        return 1
    
    print(f"Found {len(blocked_ips)} blocked IPs")
    
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
    
    # Добавляем RKN правило в routing
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    # Проверяем есть ли уже правило RKN
    rkn_exists = False
    for rule in rules:
        if rule.get('ip') and any('35.159.54.0/24' in str(ip) for ip in rule.get('ip', [])):
            rkn_exists = True
            # Обновляем правило
            rule['ip'] = blocked_ips[:500]
            print("Updated existing RKN rule", file=sys.stderr)
            break
    
    if not rkn_exists:
        # Находим позицию для вставки (ПЕРЕД outbound правилами)
        insert_index = len(rules)
        for i, rule in enumerate(rules):
            if 'inboundTag' not in rule and 'outboundTag' in rule and rule['outboundTag'] not in ['api', 'blocked']:
                insert_index = i
                break
        
        rkn_rule = {
            'type': 'field',
            'ip': blocked_ips[:500],
            'outboundTag': 'blocked'
        }
        rules.insert(insert_index, rkn_rule)
        print(f"Added RKN rule at index {insert_index}", file=sys.stderr)
    
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
