import numpy as np
import pytest

from travel_dss.catalog_api import CatalogService,JsonCatalogProvider
from travel_dss.data import ROOT
from travel_dss.domain import TripRequest,Weights
from travel_dss.routing import make_problem
from travel_dss.scenarios import ScenarioSet,build_scenarios


@pytest.fixture(scope='session')
def trip_data():
    request = TripRequest.model_validate_json((ROOT/'examples/request.json').read_text())
    data = CatalogService(JsonCatalogProvider(ROOT/'data/api/demo_catalog.json'),True).for_trip(request,'kazan')
    return request,data


def small_problem(request,data,seed):
    rng = np.random.default_rng(seed)
    n = 3+seed%3
    pois = [p.model_copy(update={'duration_min':int(rng.integers(40,100)),'duration_max':120,
                               'admission_rub':float(100+50*i)}) for i,p in enumerate(data.pois[:n])]
    prices = {p.id:data.poi_prices_rub[p.id].model_copy(update={'minimum':p.admission_rub,'typical':p.admission_rub,'maximum':p.admission_rub}) for p in pois}
    data = data.model_copy(update={'pois':pois,'poi_prices_rub':prices})
    request = request.model_copy(update={'active_minutes_day':330,'weights':Weights(interests=.7,economy=.15,weather=0,convenience=.15)})
    scenarios = build_scenarios(data,10,seed)
    problem = make_problem(data,request,data.transport_offers[0],data.stay_offers[0],scenarios)
    problem.dates = problem.dates[:1]
    problem.starts,problem.ends = [9*60],[18*60]
    problem.windows = [[[(9*60,17*60-p.duration_min)] for p in pois]]
    problem.stay = problem.stay.model_copy(update={'quote':problem.stay.quote.model_copy(update={'start':problem.stay.quote.start.replace(hour=9)})})
    problem.transport = problem.transport.model_copy(update={'destination_arrival':problem.transport.destination_arrival.replace(hour=8)})
    positions = [0]+list(rng.integers(1,20,size=n))+[0]
    problem.distances = [[abs(int(a)-int(b))*100 for b in positions] for a in positions]
    problem.minutes = [[int(np.ceil(v/80)) for v in row] for row in problem.distances]
    base = data.estimate_cost(problem.transport.quote.id,problem.stay.quote.id,[]).typical
    c = scenarios.count
    problem.scenarios = ScenarioSet(seed,np.zeros((c,1,3)),np.ones((c,1)),np.full((c,2),45),
        {problem.transport.quote.id:np.full(c,base)}, {problem.stay.quote.id:np.zeros(c)},
        np.zeros((c,1)),np.zeros((c,2)),np.tile([p.admission_rub for p in pois],(c,1)),[p.id for p in pois])
    problem.weather_utility,problem.transfer_hours = 0,5
    return problem
