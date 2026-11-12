import hashlib
import json
import platform
from time import monotonic

import numpy as np

from travel_dss.baselines import greedy_routes
from travel_dss.catalog_api import CatalogService,JsonCatalogProvider
from travel_dss.data import ROOT,write_json
from travel_dss.domain import Distribution,TripRequest,Weights
from travel_dss.preferences import utility
from travel_dss.recommendation import SearchSettings,recommend
from travel_dss.routing import make_problem,route_criteria,scenario_success
from travel_dss.scenarios import build_scenarios

request = TripRequest.model_validate_json((ROOT/'examples/request.json').read_text())
service = CatalogService(JsonCatalogProvider(ROOT/'data/api/demo_catalog.json'),True)
profiles = {
 'culture':dict(budget_rub=45000,interests=Distribution(culture=.7,history=.2,nature=.1,food=0)),
 'nature':dict(budget_rub=35000,interests=Distribution(culture=.1,history=.1,nature=.8,food=0)),
 'food':dict(budget_rub=50000,interests=Distribution(culture=.2,history=0,nature=0,food=.8)),
 'tight':dict(budget_rub=25000,good_cost_rub=15000,active_minutes_day=240),
 'comfort':dict(budget_rub=60000,max_walk_km_day=4,weights=Weights(interests=.1,economy=.2,weather=.1,convenience=.6))}
rows = []
for city in service.snapshot.destinations:
 for profile,changes in profiles.items():
    current = request.model_copy(update=changes)
    result = recommend(current,[city.id],SearchSettings(pair_seconds=.6,total_seconds=5,
        alternatives=False,closure_stress=False),allow_demo=True,use_cache=False)
    data = service.for_trip(current,city.id)
    city_seed = 42+int(hashlib.sha256(city.id.encode()).hexdigest()[:6],16)
    planning = build_scenarios(data,100,city_seed)
    evaluation = build_scenarios(data,1000,city_seed+1000003)
    row = dict(city=city.id,profile=profile,query_seconds=result.metadata['elapsed_seconds'],
               plan_count=len(result.plans),diagnostics=result.diagnostics)
    if result.plans:
        best = max(result.plans,key=lambda p:p.score)
        row.update(score=best.score,expected_cost_rub=best.expected_cost_rub,solver_status=best.solver.status,
            gap=best.solver.relative_gap,success_rate=best.risk['success_rate'],robust_by_model=best.risk['robust_by_model'])
    for method,robust in [('utility',True),('nearest',True),('without_risk',False),('without_personalization',True)]:
        best_value,best_rate = None,None
        for t in data.transport_offers:
         for stay in data.stay_offers:
            baseline_request = current.model_copy(update={'interests':Distribution()}) if method == 'without_personalization' else current
            problem = make_problem(data,baseline_request,t,stay,planning)
            routes = greedy_routes(problem,'nearest' if method == 'nearest' else 'utility',robust=robust)
            if routes is not None:
                original = make_problem(data,current,t,stay,planning)
                score = utility(route_criteria(original,routes),current.weights)
                if best_value is None or score > best_value:
                    best_value = score
                    test = make_problem(data,current,t,stay,evaluation)
                    best_rate = float(scenario_success(test,routes).mean())
        row[method+'_score'],row[method+'_success_rate'] = best_value,best_rate
    rows.append(row)
    write_json(ROOT/'reports/experiments/system_benchmark.json',{'hardware':platform.platform(),'rows':rows})
    print(city.id,profile,row.get('score'),round(row['query_seconds'],2),flush=True)

common = [r for r in rows if 'score' in r and r['utility_score'] is not None]
summary = dict(queries=len(rows),feasible_queries=sum('score' in r for r in rows),
    p95_seconds=float(np.quantile([r['query_seconds'] for r in rows],.95)),
    common_with_greedy=len(common),
    median_score_delta_vs_greedy=float(np.median([r['score']-r['utility_score'] for r in common])) if common else None,
    note='30 запросов, каждый к одному городу и всем 4 пакетам; лимит 0.6 с/пакет, 100/1000 сценариев. '
         'Эвристики и оптимизатор используют одни и те же сценарии и веса для каждого города.')
write_json(ROOT/'reports/experiments/summary.json',summary)
print(summary,flush=True)
