import hashlib
import json
from datetime import datetime, timezone
import time
from pathlib import Path

import requests

root = Path(__file__).resolve().parents[1]
for city in json.loads((root/'data/destinations.json').read_text()):
    pois = json.loads((root/f'data/processed/pois/{city["id"]}.json').read_text())
    coordinates = ';'.join(f"{p['longitude']:.6f},{p['latitude']:.6f}" for p in pois)
    path = root/f'data/raw/osrm/{city["id"]}.json'
    metadata_path = path.with_suffix('.request.json')
    request_hash = hashlib.sha256(coordinates.encode()).hexdigest()
    if metadata_path.exists() and path.exists() and json.loads(metadata_path.read_text())['coordinates_sha256'] == request_hash:
        print(city['id'], 'cached', flush=True)
        continue
    response = requests.get(f'https://routing.openstreetmap.de/routed-foot/table/v1/foot/{coordinates}',
                            params={'annotations':'duration,distance'},
                            headers={'User-Agent':'TravelDSS-AcademicPrototype/0.1'},timeout=30)
    response.raise_for_status()
    path = root/f'data/raw/osrm/{city["id"]}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(response.text)
    metadata_path.write_text(json.dumps({'source_url':response.url, 'observed_at':datetime.now(timezone.utc).isoformat(),
        'poi_ids':[p['id'] for p in pois], 'coordinates_sha256':request_hash},indent=2))
    print(city['id'], len(response.content), flush=True)
    time.sleep(1.1)
