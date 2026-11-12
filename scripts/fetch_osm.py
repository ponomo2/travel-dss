import json
from datetime import datetime, timezone

import requests

from travel_dss.data import ROOT, destinations, write_json

for city in destinations():
    query = ('[out:json][timeout:90];nwr(around:6500,' + str(city['latitude']) + ','
             + str(city['longitude']) + ')["name"]["tourism"~"^(museum|attraction|gallery)$"];out center tags;')
    response = requests.post('https://overpass-api.de/api/interpreter', data={'data': query}, timeout=120)
    response.raise_for_status()
    payload = response.json()
    write_json(ROOT / f'data/raw/osm/{city["id"]}.json', payload)
    candidates = []
    seen = set()
    for element in payload['elements']:
        tags = element['tags']
        name = tags.get('name:ru', tags['name'])
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        center = element.get('center', element)
        candidates.append(dict(id=f"osm_{element['type']}_{element['id']}", destination_id=city['id'],
                               name=name, latitude=center['lat'], longitude=center['lon'],
                               category=tags.get('tourism'), opening_hours=tags.get('opening_hours'),
                               website=tags.get('website', tags.get('contact:website')),
                               fee=tags.get('fee'), verified=False,
                               source_url=f"https://www.openstreetmap.org/{element['type']}/{element['id']}",
                               observed_at=datetime.now(timezone.utc).isoformat(), data_kind='osm'))
    write_json(ROOT / f'data/processed/poi_candidates/{city["id"]}.json', candidates)
    print(city['id'], len(candidates), flush=True)
