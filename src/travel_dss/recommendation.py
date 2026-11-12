import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Callable, List, Optional, Literal

from pydantic import Field

from .catalog_api import CatalogService, JsonCatalogProvider, TripDataset
from .data import ROOT, write_json
from .domain import Record, TripRequest
from .optimization import solve_itinerary
from .knowledge import KnowledgeBase, derive_poi_knowledge
from .decision_analysis import analyze_closure_strategy
from .preferences import explain_comparison
from .risk import assess_risk
from .routing import CandidatePlan, make_problem
from .scenarios import build_scenarios


class SearchSettings(Record):
    backend: Literal["cp_sat","pulp"] = "cp_sat"
    pair_seconds: float = Field(default=2,gt=0,le=10)
    total_seconds: float = Field(default=60,gt=0)
    planning_scenarios: int = Field(default=100,ge=1)
    evaluation_scenarios: int = Field(default=1000,ge=1)
    seed: int = Field(default=42,ge=0)
    utility_tolerance: float = Field(default=.05,ge=0,le=1)
    alternatives: bool = True
    closure_stress: bool = True


class RecommendationResult(Record):
    request: TripRequest
    plans: List[CandidatePlan]
    comparisons: List[dict]
    diagnostics: List[dict]
    metadata: dict
    suggestions: List[dict] = Field(default_factory=list)
    knowledge: dict = Field(default_factory=dict)


def _distinct(first: CandidatePlan,second: CandidatePlan) -> bool:
    if first.destination_id != second.destination_id:
        return True
    left,right = set(first.poi_ids),set(second.poi_ids)
    return bool(left|right) and len(left&right)/len(left|right) <= .7


def _dominates(first: CandidatePlan,second: CandidatePlan) -> bool:
    left = list(first.criteria.model_dump().values())+[first.risk['success_rate']]
    right = list(second.criteria.model_dump().values())+[second.risk['success_rate']]
    return all(a >= b for a,b in zip(left,right)) and any(a > b for a,b in zip(left,right))


def select_alternatives(candidates: List[CandidatePlan], tolerance: float = .05) -> List[CandidatePlan]:
    unique = {p.id:p for p in candidates}
    ordered = sorted(unique.values(),key=lambda p:(-p.score,p.id))
    if not ordered:
        return []
    best = ordered[0]
    front = [p for p in ordered if not any(_dominates(other,p) for other in ordered if other.id != p.id)]
    chosen = [best]
    nearby = front
    cheaper = [p for p in nearby if p.expected_cost_rub < best.expected_cost_rub]
    convenient = [p for p in nearby if p.criteria.convenience > best.criteria.convenience or p.risk['wilson_lower'] > best.risk['wilson_lower']]
    preferences = [sorted(cheaper,key=lambda p:p.expected_cost_rub),
                   sorted(convenient,key=lambda p:(-p.criteria.convenience,-p.risk['wilson_lower']))]
    for options in preferences:
        for plan in options:
            if all(_distinct(plan,other) for other in chosen):
                chosen.append(plan)
                break
    for plan in front:
        if len(chosen) == 3:
            break
        if all(_distinct(plan,other) for other in chosen):
            chosen.append(plan)
    return chosen[:3]


def recommend(request: TripRequest, city_ids: Optional[List[str]] = None,
              settings: Optional[SearchSettings] = None, source: Optional[Path] = None,
              allow_demo: bool = False, progress: Optional[Callable[[dict],None]] = None,
              use_cache: bool = True) -> RecommendationResult:
    settings = settings or SearchSettings()
    source = Path(source or ROOT/'data/api/demo_catalog.json')
    city_ids = city_ids or [city.id for city in CatalogService(JsonCatalogProvider(source),allow_demo).snapshot.destinations]
    key_payload = {'request':json.loads(request.model_dump_json()),'cities':city_ids,
        'settings':settings.model_dump(),'catalog':hashlib.sha256(source.read_bytes()).hexdigest(),
        'models':{city:hashlib.sha256((ROOT/f'artifacts/models/{city}.json').read_bytes()).hexdigest()
                  for city in city_ids if (ROOT/f'artifacts/models/{city}.json').exists()},
        'code':hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))).hexdigest(),
        'knowledge_sha256':KnowledgeBase().sha256,
        'allow_demo':allow_demo}
    cache_key = hashlib.sha256(json.dumps(key_payload,sort_keys=True).encode()).hexdigest()
    cache = ROOT/f'artifacts/recommendations/{cache_key}.json'
    service = CatalogService(JsonCatalogProvider(source),allow_demo)
    if use_cache and service.snapshot.mode == 'demo' and cache.exists():
        result = RecommendationResult.model_validate_json(cache.read_text())
        result.metadata['cache_hit'] = True
        return result
    if settings.backend == 'pulp':
        from .optimization_pulp import solve_pulp
        solve = solve_pulp
    else:
        solve = solve_itinerary
    started = monotonic()
    candidates, diagnostics, datasets, contexts = [],[],{},{}
    knowledge_reports = {}
    for city_id in city_ids:
        try:
            datasets[city_id] = service.for_trip(request,city_id)
            knowledge_reports[city_id] = derive_poi_knowledge(datasets[city_id],request,allow_demo)
            if any(report['facts'].get('request_conflict') for report in knowledge_reports[city_id]['entities'].values()):
                raise ValueError('База знаний выявила конфликт обязательных посещений')
            datasets[city_id] = datasets[city_id].model_copy(update={'pois':[
                poi for poi in datasets[city_id].pois
                if knowledge_reports[city_id]['entities'][poi.id]['facts'].get('eligible') is True]})
        except ValueError as error:
            datasets.pop(city_id,None)
            diagnostics.append({'city':city_id,'status':'DATA_UNAVAILABLE','reason':str(error)})
    jobs = [(data,t,s) for data in datasets.values() for t in data.transport_offers for s in data.stay_offers]
    for index,(data,transport,stay) in enumerate(jobs):
        if monotonic()-started >= settings.total_seconds:
            diagnostics.extend({'city':d.destination.id,'transport':t.quote.id,'stay':s.quote.id,'status':'NOT_RUN_TIME_LIMIT'}
                               for d,t,s in jobs[index:])
            break
        city_id = data.destination.id
        if city_id not in contexts:
            city_seed = settings.seed+int(hashlib.sha256(city_id.encode()).hexdigest()[:6],16)
            contexts[city_id] = (build_scenarios(data,settings.planning_scenarios,city_seed),
                                 build_scenarios(data,settings.evaluation_scenarios,city_seed+1000003))
        planning,evaluation = contexts[city_id]
        problem = make_problem(data,request,transport,stay,planning)
        remaining = settings.total_seconds-(monotonic()-started)
        reserve = min(12,settings.total_seconds*.2) if settings.alternatives or settings.closure_stress else 0
        limit = min(settings.pair_seconds,max(.01,(remaining-reserve)/(len(jobs)-index)))
        solved = solve(problem,limit)
        diagnostic = {'city':city_id,'transport':transport.quote.id,'stay':stay.quote.id,
                      'status':solved['status'],'elapsed_seconds':solved['elapsed_seconds']}
        if solved['plan'] is not None:
            plan = solved['plan']
            plan.risk = assess_risk(plan,make_problem(data,request,transport,stay,evaluation))
            candidates.append(plan)
        else:
            diagnostic['reason'] = solved.get('reason','')
        diagnostics.append(diagnostic)
        if progress:
            progress({'completed':index+1,'total':len(jobs),'city':data.destination.name,'candidates':len(candidates)})
    if candidates and settings.alternatives:
        best = max(candidates,key=lambda p:p.score)
        data = datasets[best.destination_id]
        transport = next(t for t in data.transport_offers if t.quote.id == best.transport_id)
        stay = next(s for s in data.stay_offers if s.quote.id == best.stay_id)
        planning,evaluation = contexts[best.destination_id]
        for mode in ('cost','convenience'):
            remaining = settings.total_seconds-(monotonic()-started)
            if remaining <= .1:
                break
            solved = solve(make_problem(data,request,transport,stay,planning),
                min(settings.pair_seconds,remaining),objective_mode=mode,min_utility=best.score-settings.utility_tolerance)
            diagnostics.append({'city':best.destination_id,'objective_mode':mode,'status':solved['status']})
            if solved['plan'] is not None:
                plan = solved['plan']
                plan.risk = assess_risk(plan,make_problem(data,request,transport,stay,evaluation))
                candidates.append(plan)
    chosen = select_alternatives(candidates,settings.utility_tolerance)
    if settings.closure_stress:
        for plan in chosen:
            remaining = settings.total_seconds-(monotonic()-started)
            if not plan.poi_ids or remaining <= .1:
                break
            closed = plan.poi_ids[0]
            if closed in request.required_poi_ids:
                plan.stress_tests.append({'closed_poi_id':closed,'status':'INFEASIBLE_REQUIRED',
                    'explanation':'Закрыт обязательный объект; заменить его без изменения предпочтений нельзя'})
                continue
            changed = request.model_copy(update={'excluded_poi_ids':request.excluded_poi_ids+[closed]})
            data = service.for_trip(changed,plan.destination_id)
            transport = next(t for t in data.transport_offers if t.quote.id == plan.transport_id)
            stay = next(s for s in data.stay_offers if s.quote.id == plan.stay_id)
            seed = contexts[plan.destination_id][0].seed+999
            planning = build_scenarios(data,settings.planning_scenarios,seed)
            solved = solve(make_problem(data,changed,transport,stay,planning),min(settings.pair_seconds,remaining))
            if solved['plan'] is None:
                plan.stress_tests.append({'closed_poi_id':closed,'status':solved['status'],
                    'explanation':'Резерв за лимит не найден; существование замены не доказано'})
            else:
                backup = solved['plan']
                evaluation = build_scenarios(data,settings.evaluation_scenarios,seed+1000003)
                backup.risk = assess_risk(backup,make_problem(data,changed,transport,stay,evaluation))
                plan.stress_tests.append({'closed_poi_id':closed,'status':'BACKUP_VALIDATED',
                    'score_difference':backup.score-plan.score,'cost_difference_rub':backup.expected_cost_rub-plan.expected_cost_rub,
                    'backup':json.loads(backup.model_dump_json()),'explanation':'Новый маршрут повторно проверен; частота закрытий не оценивалась'})
    for plan in chosen:
        plan.knowledge_trace = KnowledgeBase().infer({'subject':plan.id,
            'synthetic':plan.mode=='demo','risk_threshold_met':plan.risk['robust_by_model']})
        plan.decision_analysis = analyze_closure_strategy(plan)
    comparisons = []
    if chosen:
        for other in chosen[1:]:
            explanation = explain_comparison(chosen[0].criteria,other.criteria,request.weights)
            explanation.update(first_id=chosen[0].id,second_id=other.id)
            comparisons.append(explanation)
    metadata = {'mode':service.snapshot.mode,'notice':service.snapshot.notice,'as_of':service.snapshot.generated_at.isoformat(),
        'created_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':monotonic()-started,'cache_hit':False,
        'knowledge_sha256':key_payload['knowledge_sha256'],
        'catalog_sha256':key_payload['catalog'],'code_sha256':key_payload['code'],'model_sha256':key_payload['models'],
        'settings':settings.model_dump(),'city_count':len(city_ids),'combinations_total':len(jobs),
        'candidate_count':len(candidates),'fewer_than_three':len(chosen)<3,
        'scope':'Лучшие найденные решения; Парето-отбор только среди построенных вариантов',
        'daily_policy':'Начало не раньше 09:00, обед 12:00–15:00 около базы; ожидание входит в дневной лимит',
        'luggage_policy':'В последний день выезд из номера перед прогулкой; багаж у путешественника, трансфер забирает у базы'}
    result = RecommendationResult(request=request,plans=chosen,comparisons=comparisons,diagnostics=diagnostics,metadata=metadata,knowledge=knowledge_reports)
    if not chosen:
        result.suggestions = [{'message':'Проверьте даты и обязательные объекты; уменьшите число обязательных посещений или увеличьте лимит времени.',
                               'verified':False,'note':'Это направление изменения условий, а не доказанное решение'}]
        remaining = settings.total_seconds-(monotonic()-started)
        if jobs and remaining > .2:
            data,transport,stay = min(jobs,key=lambda job:job[0].estimate_cost(job[1].quote.id,job[2].quote.id,[]).typical)
            base = data.estimate_cost(transport.quote.id,stay.quote.id,[]).maximum
            changed_budget = max(request.budget_rub,round(base+10000))
            changed_time = max(request.active_minutes_day,360)
            changed = request.model_copy(update={'budget_rub':changed_budget,'active_minutes_day':changed_time})
            city_id = data.destination.id
            planning,evaluation = contexts.get(city_id) or (build_scenarios(data,settings.planning_scenarios,settings.seed),
                build_scenarios(data,settings.evaluation_scenarios,settings.seed+1000003))
            solved = solve(make_problem(data,changed,transport,stay,planning),min(settings.pair_seconds,remaining))
            if solved['plan'] is not None:
                repaired = solved['plan']
                repaired.risk = assess_risk(repaired,make_problem(data,changed,transport,stay,evaluation))
                result.suggestions.append({'message':f'Проверен маршрут при бюджете {changed_budget:,.0f} ₽ и дневном лимите {changed_time} мин',
                    'verified':True,'changed_budget_rub':changed_budget,'changed_active_minutes_day':changed_time,'plan':json.loads(repaired.model_dump_json()),
                    'note':'Исходный запрос не изменен; это отдельное решение при измененном ограничении'})
    result.metadata['elapsed_seconds'] = monotonic()-started
    if use_cache and service.snapshot.mode == 'demo':
        write_json(cache,json.loads(result.model_dump_json()))
    return result
