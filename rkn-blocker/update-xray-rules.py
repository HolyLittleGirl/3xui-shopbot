#!/usr/bin/env python3
"""
Добавляет RKN блокировку в routing rules Xray
"""
import json
import subprocess
import sys

XRAY_CONFIG = '/usr/local/x-ui/bin/config.json'

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
    
    try:
        with open(XRAY_CONFIG, 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        return 1
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    # Ищем НАШЕ правило RKN (не geoip:private!)
    rkn_rule_index = None
    for i, rule in enumerate(rules):
        if rule.get('outboundTag') == 'blocked':
            ip_list = rule.get('ip', [])
            # Проверяем это не geoip правило
            if ip_list and not ip_list[0].startswith('geoip:'):
                rkn_rule_index = i
                print(f"Found existing RKN rule at index {i}", file=sys.stderr)
                break
    
    if rkn_rule_index is not None:
        # Обновляем существующее правило
        rules[rkn_rule_index]['ip'] = blocked_ips[:500]
    else:
        # Создаём новое правило ПЕРЕД outbound правилами (но ПОСЛЕ domain/ip правил)
        # outbound правила не имеют 'ip' или 'domain' ключей
        insert_index = len(rules)  # По умолчанию в конец
        
        # Находим ПЕРВОЕ outbound правило (без ip и domain)
        for i, rule in enumerate(rules):
            if 'ip' not in rule and 'domain' not in rule:
                insert_index = i
                print(f"Found first outbound rule at index {i}", file=sys.stderr)
                break
        
        rkn_rule = {
            'type': 'field',
            'ip': blocked_ips[:500],
            'network': 'TCP,UDP',
            'outboundTag': 'blocked'
        }
        rules.insert(insert_index, rkn_rule)
        print(f"Added new RKN rule at index {insert_index}", file=sys.stderr)
    
    routing['rules'] = rules
    config['routing'] = routing
    
    try:
        with open(XRAY_CONFIG + '.bak', 'w') as f:
            json.dump(config, f, indent=2)
        
        with open(XRAY_CONFIG, 'w') as f:
            json.dump(config, f, indent=2)
        
        print("Config saved")
        
        subprocess.run(['systemctl', 'restart', 'x-ui'], check=True)
        print("Xray restarted")
        
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
