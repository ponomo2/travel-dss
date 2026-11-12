import hashlib
import json
import time
from datetime import datetime, timezone

import requests

from travel_dss.data import ROOT, destinations, import_osrm, write_json

for city in destinations():
    path = ROOT / f'data/processed/osrm/{city["id"]}.json'
    pois = json.loads((ROOT / f'data/processed/pois/{city["id"]}.json').read_text())
    coordinates = ';'.join(f"{p['longitude']:.6f},{p['latitude']:.6f}" for p in pois)
    url = f'https://routing.openstreetmap.de/routed-foot/table/v1/foot/{coordinates}'
    raw_path = ROOT / f'data/raw/osrm/{city["id"]}.json'
    if raw_path.exists():
        content = raw_path.read_bytes()
        request_metadata = json.loads(raw_path.with_suffix('.request.json').read_text())
        if request_metadata['poi_ids'] != [p['id'] for p in pois]:
            raise ValueError('OSRM snapshot does not match POI order; download again')
        source_url = request_metadata['source_url']
        observed_at = request_metadata['observed_at']
    else:
        response = requests.get(url, params={'annotations':'duration,distance'},
                                headers={'User-Agent':'TravelDSS-AcademicPrototype/0.1'}, timeout=45)
        response.raise_for_status()
        content = response.content
        source_url = response.url
        observed_at = datetime.now(timezone.utc).isoformat()
    payload = json.loads(content)
    version = payload.get('data_version') or 'response-sha256:' + hashlib.sha256(content).hexdigest()
    result = import_osrm(payload, [p['id'] for p in pois], 'foot', version)
    result.update(source_url=source_url, observed_at=observed_at,
                  confirmed=False, data_kind='osm',
                  confidence_note='FOSSGIS OSRM foot snapshot; graph version may be response hash; POI entrances require verification',
                  attributions=['© OpenStreetMap contributors', 'Routing by FOSSGIS OSRM'],
                  fix_map_url='https://www.openstreetmap.org/fixthemap')
    if not raw_path.exists():
        write_json(raw_path, payload)
    write_json(path, result)
    print(city['id'], len(pois), result['graph_version'], flush=True)
    time.sleep(1.1)
