"""Build a deterministic API fixture; never represents actual vendor offers."""
import hashlib
import json
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from travel_dss.catalog_api import CatalogSnapshot, catalog_coverage, store_snapshot
from travel_dss.data import ROOT, destinations, write_json
from travel_dss.osm import distance_meters


GENERATED_AT = "2026-11-12T00:00:00+03:00"
VERSION = "demo-2026-11-12-v1"
WINDOWS = json.loads((ROOT / 'data/trip_windows.json').read_text())
START, END = WINDOWS[0]['start'], WINDOWS[-1]['end']


def provenance(resource):
    return dict(source_url=f'https://travel-dss.example/api/v1/{resource}', observed_at=GENERATED_AT,
                valid_from=START, valid_to=END, data_kind='synthetic', version=VERSION,
                confidence_note='Generated demo API response; prices, hours, availability and connectors are fictional')


def price(value, lower=.85, upper=1.2):
    return dict(minimum=round(value*lower, 2), typical=value, maximum=round(value*upper, 2))


def instant(day, hour, tz):
    return datetime.combine(date.fromisoformat(day), time(hour), tzinfo=ZoneInfo(tz))


def build_snapshot():
    snapshot = dict(schema_version='1.0', mode='demo', provider='travel-dss-demo-api', generated_at=GENERATED_AT,
        currency='RUB', notice='Демонстрационный API: цены, доступность, расписания и предложения смоделированы. '
            'Названия и координаты объектов взяты из OSM. Данные не являются предложением бронирования.',
        destinations=destinations(), trip_windows=WINDOWS, pois=[], opening_intervals=[], poi_prices_rub={},
        transport_offers=[], stay_offers=[], daily_costs=[], matrices=[], original_poi_sources={})
    for number, city in enumerate(destinations()):
        city_id, tz = city['id'], city['timezone']
        source_pois = json.loads((ROOT/f'data/processed/pois/{city_id}.json').read_text())
        review = {p['id']:p for p in json.loads((ROOT/f'data/processed/pois/{city_id}.review.json').read_text())}
        pois = []
        for index, original in enumerate(source_pois):
            poi = dict(original)
            park = review[poi['id']]['_category'] == 'park'
            indoor = not park and review[poi['id']]['_category'] in ('museum', 'gallery')
            admission = 0 if park or not indoor else 350 + 100*(index % 8)
            if any(word in poi['name'].lower() for word in ('чак', 'чай', 'чая', 'пряник', 'шоколад')):
                poi['themes'] = dict(culture=.2, history=.2, nature=0, food=.6)
            poi.update(indoor=indoor, admission_rub=admission, verified=False,
                       opening_hours='Mo-Su 08:00-22:00' if not indoor else 'Tu-Su 10:00-18:00; Mo off',
                       duration_min=30 if not indoor else 60, duration_max=90 if not indoor else 120,
                       provenance=provenance('pois/'+poi['id']))
            snapshot['original_poi_sources'][poi['id']] = original['provenance']['source_url']
            snapshot['poi_prices_rub'][poi['id']] = price(admission, 1, 1.15)
            pois.append(poi)
            for window in WINDOWS:
                day = date.fromisoformat(window['start'])
                while day <= date.fromisoformat(window['end']):
                    if not indoor or day.weekday() != 0:
                        start = instant(day.isoformat(), 10 if indoor else 8, tz)
                        end = instant(day.isoformat(), 18 if indoor else 22, tz)
                        snapshot['opening_intervals'].append(dict(poi_id=poi['id'], start=start.isoformat(),
                            end=end.isoformat(), last_admission=(end-timedelta(minutes=poi['duration_min'])).isoformat(),
                            provenance=provenance('calendar/'+poi['id'])))
                    day += timedelta(days=1)
        snapshot['pois'].extend(pois)
        terminal = dict(id=city_id+'_terminal', name='Демонстрационный транспортный узел',
                        latitude=city['latitude']+.008, longitude=city['longitude'])
        hotels = [dict(id=f'{city_id}_stay_{variant}', name=f'Демонстрационное проживание {variant+1}',
                      latitude=pois[0]['latitude']+.0005*variant,
                      longitude=pois[0]['longitude']+.0005*variant) for variant in range(2)]
        duration_hours = [2, 2, 4, 4, 3, 4][number]
        transport_base = [10000, 12000, 6500, 5000, 15000, 17000][number]
        nightly_base = [4000, 3500, 3200, 2800, 3800, 4200][number]
        for window in WINDOWS:
            nights = (date.fromisoformat(window['end'])-date.fromisoformat(window['start'])).days
            season_factor = {'winter':1.0, 'spring':1.1, 'summer':1.35}[window['id']]
            for variant in range(2):
                fare = round(transport_base*season_factor*(1+.25*variant))
                departure = instant(window['start'], 9+variant, 'Europe/Moscow')
                arrival_home = instant(window['end'], 20-variant, 'Europe/Moscow')
                quote = dict(id=f'{city_id}_{window["id"]}_transport_{variant}',destination_id=city_id,kind='transport',
                    start=departure.isoformat(),end=arrival_home.isoformat(),total_rub=fare,available=True,
                    provenance=provenance('transport'))
                snapshot['transport_offers'].append(dict(quote=quote, window_id=window['id'],
                    mode='train' if city_id in ('nizhny_novgorod','yaroslavl') else 'flight',
                    destination_arrival=(departure+timedelta(hours=duration_hours)).astimezone(ZoneInfo(tz)).isoformat(),
                    return_departure=(arrival_home-timedelta(hours=duration_hours)).astimezone(ZoneInfo(tz)).isoformat(),
                    terminal=terminal,price_rub=price(fare),
                    conditions='Демонстрационный пакет туда-обратно для одного взрослого; обязательные сборы включены'))
                nightly = round(nightly_base*season_factor*(1+.4*variant))
                stay_quote = dict(id=f'{city_id}_{window["id"]}_stay_{variant}',destination_id=city_id,kind='stay',
                    start=instant(window['start'],14,tz).isoformat(),end=instant(window['end'],12,tz).isoformat(),
                    total_rub=nightly*nights,available=True,provenance=provenance('stays'))
                snapshot['stay_offers'].append(dict(quote=stay_quote,window_id=window['id'],location=hotels[variant],
                    nights=nights,nightly_rub=nightly,price_rub=price(nightly*nights),
                    conditions='Демонстрационный одноместный номер; все ночи и сборы включены, питание отдельно'))
        snapshot['daily_costs'].append(dict(destination_id=city_id,
            food_per_person_day_rub=price(1500+100*number, .7, 1.5),
            transfer_one_way_rub=price(500+100*number, .6, 1.5),
            transfer_one_way_minutes=price(45, .7, 1.5),provenance=provenance('daily-costs')))
        source_matrix = json.loads((ROOT/f'data/processed/osrm/{city_id}.json').read_text())
        poi_indices = {point:i for i, point in enumerate(source_matrix['poi_ids'])}
        points = pois+hotels+[terminal]
        distances, durations = [], []
        for left in points:
            distance_row, duration_row = [], []
            for right in points:
                if left['id'] == right['id']:
                    distance, seconds = 0, 0
                elif left['id'] in poi_indices and right['id'] in poi_indices:
                    i, j = poi_indices[left['id']], poi_indices[right['id']]
                    distance, seconds = source_matrix['distance_meters'][i][j], source_matrix['duration_seconds'][i][j]
                else:
                    distance = round(1.3*distance_meters((left['latitude'],left['longitude']),
                                                       (right['latitude'],right['longitude'])),1)
                    seconds = math.ceil(distance / (4/3.6))
                distance_row.append(distance)
                duration_row.append(seconds)
            distances.append(distance_row)
            durations.append(duration_row)
        snapshot['matrices'].append(dict(destination_id=city_id, point_ids=[p['id'] for p in points],
            distances_m=distances,durations_seconds=durations,provenance=provenance('matrices/'+city_id),
            method='POI pairs: cached FOSSGIS OSRM foot; fictional hotel/terminal connectors: geodesic x1.3 at 4 km/h'))
    return CatalogSnapshot.model_validate(snapshot)


if __name__ == '__main__':
    snapshot = build_snapshot()
    path = ROOT/'data/api/demo_catalog.json'
    write_json(path, json.loads(snapshot.model_dump_json()))
    write_json(ROOT/'data/templates/catalog_api.schema.json', CatalogSnapshot.model_json_schema())
    write_json(path.with_suffix('.metadata.json'), dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        generated_at=GENERATED_AT, mode='demo', generator='scripts/build_demo_catalog.py',
        pois=len(snapshot.pois), opening_intervals=len(snapshot.opening_intervals),
        transport_offers=len(snapshot.transport_offers),stay_offers=len(snapshot.stay_offers)))
    write_json(ROOT/'reports/data_coverage.json', catalog_coverage(snapshot))
    store_snapshot(snapshot, ROOT/'data/processed/catalog_demo.sqlite')
    print(path, len(snapshot.pois), 'POIs', len(snapshot.transport_offers)+len(snapshot.stay_offers), 'offers')
