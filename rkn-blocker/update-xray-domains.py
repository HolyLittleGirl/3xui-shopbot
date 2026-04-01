#!/usr/bin/env python3
"""
Добавляет доменную блокировку ПЕРЕД outbound правилами
"""
import json
import sys

XRAY_CONFIG = '/usr/local/x-ui/bin/config.json'

BLOCKED_DOMAINS = [
    'domain:facebook.com',
    'domain:fbcdn.net',
    'domain:instagram.com',
    'domain:twitter.com',
    'domain:x.com',
    'domain:tiktok.com',
    'domain:youtube.com',
    'regexp:.*\\.(facebook|fbcdn|instagram)\\.(com|net)$'
]

def main():
    try:
        with open(XRAY_CONFIG, 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        return 1
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    # Удаляем старое правило если есть
    rules = [r for r in rules if not (r.get('domain') and any('facebook' in str(d) for d in r.get('domain', [])))]
    
    # Находим ПЕРВОЕ outbound правило (без IP и domain)
    insert_index = len(rules)
    for i, rule in enumerate(rules):
        has_ip = 'ip' in rule
        has_domain = 'domain' in rule
        if not has_ip and not has_domain:
            insert_index = i
            print(f"Found first outbound at index {i}", file=sys.stderr)
            break
    
    domain_rule = {
        'type': 'field',
        'domain': BLOCKED_DOMAINS,
        'network': 'TCP,UDP',
        'outboundTag': 'blocked'
    }
    rules.insert(insert_index, domain_rule)
    print(f"Added domain rule at index {insert_index}", file=sys.stderr)
    
    routing['rules'] = rules
    config['routing'] = routing
    
    try:
        with open(XRAY_CONFIG, 'w') as f:
            json.dump(config, f, indent=2)
        print("Config saved")
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
