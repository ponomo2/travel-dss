import copy
import json
from datetime import datetime, timedelta

import pytest
import requests
from pydantic import ValidationError

from travel_dss.catalog_api import (CatalogService, CatalogSnapshot, HttpCatalogProvider, JsonCatalogProvider,
                                    PriceRange, SqliteCatalogProvider, catalog_coverage, save_snapshot, store_snapshot)
from travel_dss.data import ROOT
from travel_dss.domain import TripRequest


@pytest.fixture(scope='module')
def payload():
    return json.loads((ROOT/'data/api/demo_catalog.json').read_text())


@pytest.fixture(scope='module')
def service():
    return CatalogService(JsonCatalogProvider(ROOT/'data/api/demo_catalog.json'), allow_demo=True)


def request_for(window):
    return TripRequest(departure=f'{window.start}T08:00:00+03:00', return_by=f'{window.end}T22:00:00+03:00',
                       budget_rub=45000)


def test_explicit_demo_opt_in():
    with pytest.raises(ValueError, match='allow_demo'):
        CatalogService(JsonCatalogProvider(ROOT/'data/api/demo_catalog.json'))


def test_complete_data_for_all_cities_and_dates(service):
    for city in service.snapshot.destinations:
        for window in service.snapshot.trip_windows:
            dataset = service.for_trip(request_for(window),city.id)
            assert len(dataset.transport_offers) == len(dataset.stay_offers) == 2
            assert 20 <= len(dataset.pois) <= 30
            assert all(p.admission_rub is not None and p.indoor is not None for p in dataset.pois)
            assert all(not p.confirmed and not p.verified for p in dataset.pois)
            assert len(dataset.matrix.point_ids) == len(dataset.pois)+3
            assert dataset.as_of == service.snapshot.generated_at
            assert not dataset.transport_offers[0].quote.usable(dataset.as_of)
            assert dataset.transport_offers[0].quote.usable(dataset.as_of,allow_demo=True)
    report = catalog_coverage(service.snapshot)
    assert report['demo_data_ready'] and report['status'] == 'complete_demo'
    assert not report['confirmed_real_data_ready']
    assert report['transport_offers'] == report['stay_offers'] == 36


def test_full_cost_accounting_and_distances(service):
    request = request_for(service.snapshot.trip_windows[1])
    dataset = service.for_trip(request,'kazan')
    transport, stay = dataset.transport_offers[0], dataset.stay_offers[0]
    selected = [p.id for p in dataset.pois[:3]]
    estimate = dataset.estimate_cost(transport.quote.id,stay.quote.id,selected)
    expected = (transport.quote.total_rub+stay.quote.total_rub
                +request.days*dataset.daily_costs.food_per_person_day_rub.typical
                +2*dataset.daily_costs.transfer_one_way_rub.typical
                +sum(dataset.poi_prices_rub[p].typical for p in selected))
    assert estimate.typical == expected
    assert estimate.minimum <= estimate.typical <= estimate.maximum
    assert dataset.travel_between(selected[0],selected[0]) == {'distance_m':0,'duration_seconds':0}
    edge = dataset.travel_between(stay.location.id,selected[0])
    assert edge['distance_m'] is not None
    with pytest.raises(ValueError):
        dataset.estimate_cost(transport.quote.id,stay.quote.id,[selected[0],selected[0]])
    with pytest.raises(ValueError):
        dataset.estimate_cost('unknown',stay.quote.id,[])
    with pytest.raises(ValueError):
        dataset.travel_between('unknown',selected[0])


def test_closed_museum_and_open_park_are_distinct(service):
    window = service.snapshot.trip_windows[1]
    dataset = service.for_trip(request_for(window),'kazan')
    museum = next(p for p in dataset.pois if p.indoor)
    park = next(p for p in dataset.pois if not p.indoor)
    assert window.end.weekday() == 0
    assert not dataset.opening_windows(museum.id,window.end)
    assert dataset.opening_windows(park.id,window.end)
    assert dataset.opening_windows(museum.id,window.start)
    with pytest.raises(ValueError):
        dataset.opening_windows(museum.id,window.end+timedelta(days=1))


def test_date_time_availability_and_expiry(service):
    window = service.snapshot.trip_windows[1]
    request = request_for(window)
    with pytest.raises(ValueError,match='актуальных'):
        service.for_trip(request,'kazan',as_of=service.snapshot.generated_at+timedelta(hours=25))
    with pytest.raises(ValueError,match='момент'):
        service.for_trip(request,'kazan',as_of=datetime(2026,11,12))
    late = request.model_copy(update={'departure':request.departure+timedelta(hours=5)})
    with pytest.raises(ValueError,match='актуальных'):
        service.for_trip(late,'kazan')
    other_dates = TripRequest(departure='2027-04-17T08:00:00+03:00',return_by='2027-04-19T22:00:00+03:00',budget_rub=45000)
    with pytest.raises(ValueError,match='даты'):
        service.for_trip(other_dates,'kazan')


def test_exclusions_and_required_objects(service):
    request = request_for(service.snapshot.trip_windows[1])
    dataset = service.for_trip(request,'kazan')
    excluded_id = dataset.pois[0].id
    excluded_request = request.model_copy(update={'excluded_poi_ids':[excluded_id]})
    assert excluded_id not in {p.id for p in service.for_trip(excluded_request,'kazan').pois}
    required_request = request.model_copy(update={'required_poi_ids':['not-in-city']})
    with pytest.raises(ValueError,match='Обязательные'):
        service.for_trip(required_request,'kazan')


@pytest.mark.parametrize('problem', ['live','duplicate','unknown-city','missing-price','negative-price',
                                    'bad-window','bad-matrix','unknown-calendar-poi','verified-demo'])
def test_reject_inconsistent_snapshots(payload,problem):
    value = copy.deepcopy(payload)
    if problem == 'live': value['mode'] = 'live'
    elif problem == 'duplicate': value['pois'].append(value['pois'][0])
    elif problem == 'unknown-city': value['pois'][0]['destination_id'] = 'unknown'
    elif problem == 'missing-price': value['poi_prices_rub'].pop(value['pois'][0]['id'])
    elif problem == 'negative-price': value['daily_costs'][0]['food_per_person_day_rub']['minimum'] = -1
    elif problem == 'bad-window': value['transport_offers'][0]['window_id'] = 'unknown'
    elif problem == 'bad-matrix': value['matrices'][0]['point_ids'][0] = 'unknown'
    elif problem == 'unknown-calendar-poi': value['opening_intervals'][0]['poi_id'] = 'unknown'
    elif problem == 'verified-demo': value['pois'][0]['verified'] = True
    with pytest.raises(ValidationError):
        CatalogSnapshot.model_validate(value)


def test_json_and_sqlite_roundtrip(service,tmp_path):
    json_path = tmp_path/'snapshot.json'
    database = tmp_path/'catalog.sqlite'
    save_snapshot(service.snapshot,json_path)
    store_snapshot(service.snapshot,database)
    assert JsonCatalogProvider(json_path).load() == service.snapshot
    assert SqliteCatalogProvider(database).load() == service.snapshot
    store_snapshot(service.snapshot,database)
    assert SqliteCatalogProvider(database).load() == service.snapshot


def test_http_contract_without_external_account(payload):
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    class Session:
        def get(self,url,**kwargs):
            assert url == 'https://api.example/catalog'
            assert kwargs['timeout'] == 30
            return Response()
    snapshot = HttpCatalogProvider('https://api.example/catalog',session=Session()).load()
    assert snapshot.mode == 'demo'
    with pytest.raises(ValueError,match='HTTPS'):
        HttpCatalogProvider('http://api.example/catalog')


def test_http_errors_are_not_replaced_with_fake_data():
    class Response:
        def raise_for_status(self): raise requests.HTTPError('503 service unavailable')
    class Session:
        def get(self,*args,**kwargs): return Response()
    with pytest.raises(requests.HTTPError):
        HttpCatalogProvider('https://api.example/catalog',session=Session()).load()


def test_unavailable_offers_are_filtered(payload,tmp_path):
    value = copy.deepcopy(payload)
    for offer in value['transport_offers']:
        if offer['quote']['destination_id'] == 'kazan' and offer['window_id'] == 'spring':
            offer['quote']['available'] = False
    path = tmp_path/'unavailable.json'
    path.write_text(json.dumps(value))
    catalog = CatalogService(JsonCatalogProvider(path),allow_demo=True)
    with pytest.raises(ValueError,match='актуальных'):
        catalog.for_trip(request_for(catalog.snapshot.trip_windows[1]),'kazan')


def test_precheck_accepts_demo_only_when_requested(service):
    from travel_dss.validation import precheck_catalog
    request = request_for(service.snapshot.trip_windows[1])
    dataset = service.for_trip(request,'kazan')
    quotes = [o.quote for o in dataset.transport_offers+dataset.stay_offers]
    assert not precheck_catalog(request,dataset.pois,quotes,dataset.as_of,allow_demo=True)
    assert precheck_catalog(request,dataset.pois,quotes,dataset.as_of)


def test_cli_uses_api_data(capsys,monkeypatch,tmp_path):
    from travel_dss.cli import main
    target = tmp_path/'trip.json'
    monkeypatch.setattr('sys.argv',['travel-dss','catalog','kazan','--allow-demo','--output',str(target)])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'demo'
    assert result['base_cost_estimates'][0]['cost_without_admissions']['typical'] == 32350
    assert json.loads(target.read_text())['mode'] == 'demo'


def test_coverage_does_not_accept_unavailable_offers(payload):
    value = copy.deepcopy(payload)
    for offer in value['stay_offers']:
        if offer['quote']['destination_id'] == 'sochi' and offer['window_id'] == 'summer':
            offer['quote']['available'] = False
    report = catalog_coverage(CatalogSnapshot.model_validate(value))
    assert not report['demo_data_ready']
    assert report['status'] == 'requires_review'


def test_cli_demo_requires_opt_in_with_clean_error(capsys,monkeypatch):
    from travel_dss.cli import main
    monkeypatch.setattr('sys.argv',['travel-dss','catalog','kazan'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert 'allow_demo' in stderr and 'Traceback' not in stderr
