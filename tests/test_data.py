import pytest
from travel_dss.data import import_osrm


def test_osrm_unreachable_pairs_are_preserved():
    payload = {'code':'Ok', 'durations':[[0,None],[60,0]], 'distances':[[0,None],[100,0]]}
    result = import_osrm(payload, ['a','b'], 'foot', 'snapshot-1')
    assert result['duration_seconds'][0][1] is None
    with pytest.raises(ValueError):
        import_osrm(payload, ['a','b'], 'car', 'snapshot-1')
    with pytest.raises(ValueError):
        import_osrm(payload, ['a'], 'foot', 'snapshot-1')


def test_import_real_candidate_catalog():
    from travel_dss.data import ROOT, destinations, import_records
    from travel_dss.domain import POI
    for city in destinations():
        pois = import_records(ROOT/f'data/processed/pois/{city["id"]}.json', POI)
        assert 20 <= len(pois) <= 30
        assert not any(p.confirmed for p in pois)
        assert all(p.admission_rub is None for p in pois)


def test_quote_csv_roundtrip(tmp_path):
    import csv
    from travel_dss.data import import_quote_csv
    row = dict(id='test', destination_id='kazan', kind='transport', start='2027-04-16T09:00:00+03:00',
               end='2027-04-19T19:00:00+03:00',total_rub=10000,available='true',
               source_url='https://example.org/quotation', observed_at='2026-11-12T12:00:00+03:00',
               valid_from='2027-04-16',valid_to='2027-04-19',confidence_note='fixture',version='1')
    path = tmp_path/'quotes.csv'
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f,fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    assert import_quote_csv(path)[0].total_rub == 10000


def test_real_osrm_snapshot_order_and_shape():
    import json
    from travel_dss.data import ROOT, destinations, import_osrm
    for city in destinations():
        city_id = city['id']
        pois = json.loads((ROOT/f'data/processed/pois/{city_id}.json').read_text())
        payload = json.loads((ROOT/f'data/processed/osrm/{city_id}.json').read_text())
        assert payload['poi_ids'] == [p['id'] for p in pois]
        raw = json.loads((ROOT/f'data/raw/osrm/{city_id}.json').read_text())
        imported = import_osrm(raw,payload['poi_ids'],'foot',payload['graph_version'])
        assert imported['duration_seconds'] == payload['duration_seconds']
        assert not payload['confirmed']
