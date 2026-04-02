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
        print(f"Error loading outbounds: {e}")
    
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
            print(f"Failed to resolve {host}: {e}")
    
    return outbound_ips

def get_blocked_ips():
    """Получить IP из antifilter, исключая наши аутбаунды"""
    our_ips = get_our_outbound_ips()
    print(f"Our outbound IPs/subnets to exclude: {len(our_ips)}")
    for ip in list(our_ips)[:10]:
        print(f"  Exclude: {ip}")
    
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
        
        print(f"Excluded {excluded_count} IPs/subnets that match our outbounds")
        print(f"Total blocked IPs: {len(blocked_ips)}")
        
        return blocked_ips[:1000]  # Лимит 1000 IP (Xray имеет ограничения)
    except Exception as e:
        print(f"Error loading IPs: {e}")
        return []

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update-3xui-rkn-ips.py [enable|disable]")
        return 1
    
    action = sys.argv[1]
    
    # Читаем конфиг из базы
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = cursor.fetchone()
    
    if not row:
        print("xrayTemplateConfig not found in database")
        return 1
    
    try:
        config = json.loads(row[0])
    except Exception as e:
        print(f"Error parsing config: {e}")
        return 1
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    if action == 'enable':
        ips = get_blocked_ips()
        if not ips:
            print("No IPs found")
            return 1
        
        # Сначала удаляем старое RKN правило если есть
        new_rules = []
        for rule in rules:
            ips_count = len(rule.get('ip', []))
            outbound = rule.get('outboundTag')
            # Пропускаем старые RKN правила
            if ips_count > 100 and outbound == 'direct':
                continue
            new_rules.append(rule)
        rules = new_rules
        
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
        }
        rules.insert(insert_index, rkn_rule)
        print(f"Added RKN IP rule at index {insert_index} with {len(ips)} IPs")
    
    elif action == 'disable':
        # Удаляем RKN правила (первое правило с >100 IP и outboundTag: direct)
        original_count = len(rules)
        new_rules = []
        removed_count = 0
        rkn_removed = False
        for rule in rules:
            # Проверяем не RKN ли это правило
            ips = rule.get('ip', [])
            outbound = rule.get('outboundTag')
            is_rkn = (len(ips) > 100 and outbound == 'direct')
            
            if is_rkn and not rkn_removed:
                removed_count += 1
                rkn_removed = True
                continue  # Пропускаем это правило
            new_rules.append(rule)
        rules = new_rules
        print(f"Removed {removed_count} RKN IP rules")
    
    routing['rules'] = rules
    config['routing'] = routing
    
    # Сохраняем в базу
    try:
        cursor.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(config),)
        )
        conn.commit()
        print("Config saved to database")
        
    except Exception as e:
        print(f"Error saving config: {e}")
        return 1
    finally:
        conn.close()
    
    # Перезагружаем x-ui (stop → start чтобы перечитал конфиг из базы)
    try:
        subprocess.run(['systemctl', 'stop', 'x-ui'], check=True, timeout=30)
        import time
        time.sleep(2)
        subprocess.run(['systemctl', 'start', 'x-ui'], check=True, timeout=30)
        print("Xray restarted")
    except Exception as e:
        print(f"Error restarting x-ui: {e}")
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
