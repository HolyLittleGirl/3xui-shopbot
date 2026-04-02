#!/usr/bin/env python3
"""
Добавляет/удаляет RKN блокировку IP в конфиге 3x-ui через базу данных
Usage:
    python3 update-3xui-rkn-ips.py enable   - Добавить RKN правило
    python3 update-3xui-rkn-ips.py disable  - Удалить RKN правило
"""
import json
import sqlite3
import subprocess
import sys

DB_PATH = '/etc/x-ui/x-ui.db'
IPS_FILE = '/tmp/rkn_ips.txt'
OUTBOUNDS_FILE = '/tmp/our_outbounds.txt'

def get_our_outbound_ips():
    """Получить IP наших аутбаундов которые нужно исключить"""
    outbound_hosts = []
    try:
        with open(OUTBOUNDS_FILE, 'r') as f:
            for line in f:
                if ':' in line:
                    host = line.strip().split(': ')[1].strip()
                    outbound_hosts.append(host)
    except Exception as e:
        print(f"Error loading outbounds: {e}", file=sys.stderr)
    
    # Резолвим хосты в IP
    outbound_ips = set()
    for host in outbound_hosts:
        try:
            import socket
            ips = socket.gethostbyname_ex(host)
            for ip in ips[2]:
                outbound_ips.add(ip)
                # Добавляем подсеть /24 для этого IP
                ip_parts = ip.split('.')
                subnet = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.0/24"
                outbound_ips.add(subnet)
        except Exception as e:
            print(f"Failed to resolve {host}: {e}", file=sys.stderr)
    
    return outbound_ips

def get_blocked_ips():
    """Получить IP из antifilter, исключая наши аутбаунды"""
    our_ips = get_our_outbound_ips()
    print(f"Our outbound IPs/subnets to exclude: {len(our_ips)}", file=sys.stderr)
    for ip in list(our_ips)[:10]:
        print(f"  Exclude: {ip}", file=sys.stderr)
    
    try:
        with open(IPS_FILE, 'r') as f:
            all_ips = [line.strip() for line in f if line.strip()]
        
        # Исключаем наши IP
        blocked_ips = []
        excluded_count = 0
        for ip in all_ips:
            # Проверяем не попадает ли IP в наши подсети
            is_ours = False
            for our_ip in our_ips:
                if our_ip.endswith('/24'):
                    # Проверяем подсеть
                    our_prefix = our_ip[:-3]  # Убираем .0/24
                    if ip.startswith(our_prefix[:ip.rfind('.')+1]):
                        is_ours = True
                        excluded_count += 1
                        break
                elif ip == our_ip:
                    is_ours = True
                    excluded_count += 1
                    break
            
            if not is_ours:
                blocked_ips.append(ip)
        
        print(f"Excluded {excluded_count} IPs/subnets that match our outbounds", file=sys.stderr)
        print(f"Total blocked IPs: {len(blocked_ips)}", file=sys.stderr)
        
        return blocked_ips[:1000]  # Лимит 1000 IP (Xray имеет ограничения)
    except Exception as e:
        print(f"Error loading IPs: {e}", file=sys.stderr)
        return []

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update-3xui-rkn-ips.py [enable|disable]", file=sys.stderr)
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
        ips = get_blocked_ips()
        if not ips:
            print("No IPs found", file=sys.stderr)
            return 1
        
        # Проверяем есть ли уже правило RKN
        rkn_exists = False
        for rule in rules:
            if rule.get('_rkn_ips_blocker'):
                rkn_exists = True
                # Обновляем правило
                rule['ip'] = ips
                print(f"Updated existing RKN IP rule with {len(ips)} IPs", file=sys.stderr)
                break
        
        if not rkn_exists:
            # Находим позицию для вставки (ПЕРЕД outbound правилами)
            insert_index = 0
            for i, rule in enumerate(rules):
                if 'inboundTag' not in rule and 'outboundTag' in rule:
                    insert_index = i
                    break
            
            rkn_rule = {
                'type': 'field',
                'ip': ips,
                'outboundTag': 'direct',  # Прямое подключение через сервер в РФ (заблокировано провайдером)
                '_rkn_ips_blocker': True  # Маркер что это RKN правило
            }
            rules.insert(insert_index, rkn_rule)
            print(f"Added RKN IP rule at index {insert_index} with {len(ips)} IPs", file=sys.stderr)
    
    elif action == 'disable':
        # Удаляем RKN правила (с маркером '_rkn_ips_blocker')
        original_count = len(rules)
        rules = [
            rule for rule in rules
            if not rule.get('_rkn_ips_blocker')
        ]
        removed_count = original_count - len(rules)
        print(f"Removed {removed_count} RKN IP rules", file=sys.stderr)
    
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
