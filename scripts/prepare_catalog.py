from travel_dss.data import ROOT, destinations, import_records, store_catalog, write_json
from travel_dss.domain import POI
from travel_dss.osm import prepare_city

report, records = [], []
for city in destinations():
    coverage = prepare_city(city)
    report.append(coverage)
    records.extend(import_records(ROOT / f'data/processed/pois/{city["id"]}.json', POI))
    print(coverage, flush=True)
store_catalog(records, [], ROOT/'data/processed/catalog.sqlite')
write_json(ROOT/'reports/osm_candidate_coverage.json', {'cities':report, 'quotes':0,
    'planned_windows': ['2027-01-22/2027-01-25','2027-04-16/2027-04-19','2027-07-16/2027-07-19'],
    'confirmed_route_ready':False,
    'osrm_snapshots':sum((ROOT/f'data/processed/osrm/{city["id"]}.json').exists() for city in destinations()),
    'missing':['Official POI verification', 'User-supplied quotes', 'POI entrances and matrix validation'],
    'attribution':'© OpenStreetMap contributors; ODbL; https://www.openstreetmap.org/copyright'})
