"""Opt-in normalization for one documented, malformed proxy upgrade response.

This never disables TLS verification or Sec-WebSocket-Accept validation. Prefer
fixing the proxy. Leave the workaround OFF unless the exact failure is observed.
"""

def normalize_close_upgrade(response) -> bool:
    headers=response.headers
    connections=[part.strip().lower() for value in headers.get_all('Connection')
                 for part in value.split(',') if part.strip()]
    upgrades=[part.strip().lower() for value in headers.get_all('Upgrade')
              for part in value.split(',') if part.strip()]
    if response.status_code != 101 or connections != ['close'] or upgrades != ['websocket']:
        return False
    del headers['Connection']
    headers['Connection']='Upgrade'
    return True
