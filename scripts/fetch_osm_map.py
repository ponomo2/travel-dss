import argparse
import gzip
from datetime import datetime, timezone

import requests

from travel_dss.data import ROOT, destinations, write_json

parser = argparse.ArgumentParser()
parser.add_argument("--city")
args = parser.parse_args()
for city in destinations():
    if args.city and city["id"] != args.city:
        continue
    lat, lon = city['latitude'], city['longitude']
    params = {'bbox': f'{lon-.015},{lat-.01},{lon+.015},{lat+.01}'}
    response = requests.get('https://api.openstreetmap.org/api/0.6/map', params=params,
                            headers={'User-Agent':'TravelDSS-AcademicPrototype/0.1'}, timeout=120)
    response.raise_for_status()
    path = ROOT / f'data/raw/osm/{city["id"]}.osm.gz'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(response.content, mtime=0))
    write_json(path.with_suffix('.metadata.json'), {'source_url':response.url,
        'observed_at':datetime.now(timezone.utc).isoformat(), 'data_kind':'osm', 'version':'1',
        'license':'ODbL', 'attribution':'© OpenStreetMap contributors',
        'confidence_note':'Limited city-center extract; OSM tags require official verification'})
    print(city['id'], len(response.content), flush=True)
