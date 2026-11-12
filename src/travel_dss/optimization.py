import hashlib
from math import ceil, floor
from time import monotonic
from typing import List, Optional

import numpy as np
from ortools.sat.python import cp_model

from .preferences import utility
from .routing import CandidatePlan, RoutingProblem, SolverInfo, route_criteria, simulate_day, validate_plan


SCALE = 10000


def solve_itinerary(problem: RoutingProblem, time_limit: float = 10, objective_mode: str = 'utility',
                    min_utility: Optional[float] = None, excluded_sets: Optional[List[List[str]]] = None) -> dict:
    if time_limit <= 0 or objective_mode not in ('utility', 'cost', 'convenience'):
        raise ValueError('Некорректные параметры решателя')
    started = monotonic()
    model = cp_model.CpModel()
    n, meal, count = problem.n, problem.meal, problem.scenarios.count
    request = problem.request
    selected = [model.NewBoolVar(f'selected_{i}') for i in range(n)]
    model.Add(sum(selected) >= 1)
    y, arcs = {}, {}
    for d in range(len(problem.dates)):
        day_arcs = []
        for i in range(1, meal+1):
            y[d,i] = model.NewBoolVar(f'visit_{d}_{i}')
            if i == meal:
                model.Add(y[d,i] == 1)
            elif not problem.node_windows(i,d):
                model.Add(y[d,i] == 0)
            day_arcs.append((i,i,y[d,i].Not()))
        for i in range(meal+1):
            for j in range(meal+1):
                if i == j or problem.minutes[i][j] is None:
                    continue
                arc = model.NewBoolVar(f'arc_{d}_{i}_{j}')
                arcs[d,i,j] = arc
                day_arcs.append((i,j,arc))
        model.AddCircuit(day_arcs)
        model.Add(sum(problem.distances[i][j]*arc for (day,i,j),arc in arcs.items() if day == d)
                  <= floor(request.max_walk_km_day*1000))
    for i,p in enumerate(problem.data.pois):
        model.Add(sum(y[d,i+1] for d in range(len(problem.dates))) == selected[i])
        if p.id in request.required_poi_ids:
            model.Add(selected[i] == 1)
        if p.id in request.excluded_poi_ids:
            model.Add(selected[i] == 0)
    for previous in excluded_sets or []:
        indices = [i for i,p in enumerate(problem.data.pois) if p.id in previous]
        model.Add(sum(selected[i] for i in indices)*10 <= floor(.7*len(previous))*10)
    nominal_base = problem.data.estimate_cost(problem.transport.quote.id,problem.stay.quote.id,[]).typical
    model.Add(ceil(nominal_base)+sum(ceil(p.admission_rub)*selected[i] for i,p in enumerate(problem.data.pois))
              <= floor(request.budget_rub))
    base_samples = problem.scenarios.base_costs(problem.transport.quote.id,problem.stay.quote.id)
    rejected = [model.NewBoolVar(f'reject_{s}') for s in range(count)]
    model.Add(sum(rejected) <= floor((1-request.min_success_rate)*count+1e-8))
    for s in range(count):
        model.Add(ceil(base_samples[s])+sum(ceil(problem.scenarios.admission_rub[s,i])*selected[i] for i in range(n))
                  <= floor(request.budget_rub)).OnlyEnforceIf(rejected[s].Not())

    def add_schedule(scenario=None):
        for d in range(len(problem.dates)):
            start, end = problem.day_bounds(d, scenario)
            enforce = [] if scenario is None else [rejected[scenario].Not()]
            times = {i:model.NewIntVar(0, 2880, f't_{scenario}_{d}_{i}') for i in range(1,meal+1)}
            order = {i:model.NewIntVar(1,meal,f'order_{scenario}_{d}_{i}') for i in range(1,meal+1)}
            for i in range(1,meal+1):
                windows = []
                for low, high in problem.node_windows(i,d):
                    if high < low:
                        continue
                    window = model.NewBoolVar(f'window_{scenario}_{d}_{i}_{low}')
                    windows.append(window)
                    model.Add(times[i] >= low).OnlyEnforceIf(enforce+[window])
                    model.Add(times[i] <= high).OnlyEnforceIf(enforce+[window])
                if windows:
                    model.Add(sum(windows) == y[d,i]).OnlyEnforceIf(enforce)
                else:
                    model.Add(y[d,i] == 0).OnlyEnforceIf(enforce)
            for (day,i,j),arc in arcs.items():
                if day != d:
                    continue
                travel = problem.travel(i,j,d,scenario)
                duration = problem.duration(i)
                if i == 0:
                    model.Add(times[j] >= start+travel).OnlyEnforceIf(enforce+[arc])
                elif j == 0:
                    model.Add(times[i]+duration+travel <= min(end,start+request.active_minutes_day)).OnlyEnforceIf(enforce+[arc])
                else:
                    model.Add(times[j] >= times[i]+duration+travel).OnlyEnforceIf(enforce+[arc])
                    model.Add(order[j] > order[i]).OnlyEnforceIf(enforce+[arc])
    add_schedule()
    expected_base = ceil(float(base_samples.mean()))
    expected_admissions = [ceil(float(problem.scenarios.admission_rub[:,i].mean())) for i in range(n)]
    max_cost = max(1, expected_base+sum(expected_admissions))
    expected_cost = model.NewIntVar(0,max_cost,'expected_cost')
    model.Add(expected_cost == expected_base+sum(expected_admissions[i]*selected[i] for i in range(n)))
    criteria_terms = {}
    interests = []
    for theme, weight in request.interests.model_dump().items():
        raw = sum(round(p.themes.model_dump()[theme]*1000)*selected[i] for i,p in enumerate(problem.data.pois))
        capped = model.NewIntVar(0,request.days*1000,'theme_'+theme)
        model.AddMinEquality(capped,[raw,request.days*1000])
        interests.append((weight,capped))
    good = request.good_cost_rub if request.good_cost_rub is not None else .6*request.budget_rub
    economical = model.NewIntVar(0,ceil(request.budget_rub-good),'economical')
    positive = model.NewIntVar(0,max(ceil(request.budget_rub),max_cost),'positive')
    model.AddMaxEquality(positive,[0,floor(request.budget_rub)-expected_cost])
    model.AddMinEquality(economical,[positive,ceil(request.budget_rub-good)])
    walking = sum(problem.distances[i][j]*arc for (d,i,j),arc in arcs.items())
    # Common positive scaling preserves the weighted objective; integer rounding is reported.
    objective_scale = 100000000
    expression = sum(round(objective_scale*request.weights.interests*w/(request.days*1000))*v for w,v in interests)
    expression += round(objective_scale*request.weights.economy/(request.budget_rub-good))*economical
    expression -= round(objective_scale*request.weights.convenience*.5/(request.days*request.max_walk_km_day*1000))*walking
    constant = round(objective_scale*(request.weights.weather*problem.weather_utility
                      +request.weights.convenience*(1-.5*min(1,problem.transfer_hours/24))))
    expression += constant
    objective_var = model.NewIntVar(-10**10,10**10,'utility')
    model.Add(objective_var == expression)
    if min_utility is not None:
        model.Add(objective_var >= ceil((min_utility+0.001)*objective_scale))
    if objective_mode == 'utility':
        model.Maximize(objective_var)
    elif objective_mode == 'cost':
        model.Minimize(expected_cost)
    else:
        model.Minimize(walking)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = problem.scenarios.seed % (2**31-1)
    solver.parameters.relative_gap_limit = 0
    active_scenarios = set()
    from .baselines import greedy_routes
    seed_routes = greedy_routes(problem)
    incumbent = None
    if seed_routes is not None and objective_mode == 'utility' and min_utility is None and not excluded_sets:
        seed_validation = validate_plan(problem,seed_routes)
        seed_value = utility(route_criteria(problem,seed_routes),request.weights)
        incumbent = (seed_routes,seed_validation,seed_value*objective_scale)
        for d,route in enumerate(seed_routes):
            pairs = set(zip([0]+route,route+[0]))
            for (day,i,j),arc in arcs.items():
                if day == d:
                    model.AddHint(arc,int((i,j) in pairs))
            for i in range(1,meal+1):
                model.AddHint(y[d,i],int(i in route))
        for i,variable in enumerate(selected):
            model.AddHint(variable,int(any(i+1 in r for r in seed_routes)))
    accepted_solver = False
    final_status = 'UNKNOWN'
    bound = None
    while monotonic()-started < time_limit:
        solver.parameters.max_time_in_seconds = max(.01,time_limit-(monotonic()-started))
        status = solver.Solve(model)
        final_status = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            break
        bound = solver.BestObjectiveBound()
        routes = []
        for d in range(len(problem.dates)):
            route, previous = [],0
            for _ in range(meal+1):
                following = [j for (day,i,j),arc in arcs.items() if day == d and i == previous and solver.BooleanValue(arc)]
                if len(following) != 1:
                    raise RuntimeError('Некорректная цепочка решателя')
                previous = following[0]
                if previous == 0:
                    break
                route.append(previous)
            routes.append(route)
        validation = validate_plan(problem,routes)
        if validation['nominal_valid'] and validation['success_rate']+1e-9 >= request.min_success_rate:
            if incumbent is not None and objective_mode == 'utility':
                if utility(route_criteria(problem,routes),request.weights)+.001 < utility(route_criteria(problem,incumbent[0]),request.weights):
                    break
            actual_objective = solver.Value(objective_var) if objective_mode == 'utility' else solver.Value(expected_cost) if objective_mode == 'cost' else solver.Value(walking)
            if abs(solver.ObjectiveValue()-actual_objective) > 1:
                final_status = 'FEASIBLE'
                bound = None
            incumbent = (routes,validation,actual_objective)
            accepted_solver = True
            break
        violated = [s for s, success in enumerate(validation['success']) if not success and s not in active_scenarios]
        if not violated:
            break
        for s in violated:
            add_schedule(s)
            active_scenarios.add(s)
        model.ClearHints()
        for variable in selected:
            model.AddHint(variable,solver.Value(variable))
    elapsed = monotonic()-started
    if incumbent is None:
        return {'plan':None,'status':final_status,'elapsed_seconds':elapsed,
                'reason':'Ограничения несовместимы' if final_status == 'INFEASIBLE' else 'За лимит не найден маршрут, прошедший все проверки'}
    routes, validation, value = incumbent
    if not accepted_solver:
        final_status = 'FEASIBLE'
        bound = None
    criteria = route_criteria(problem,routes)
    scaled_value = value/objective_scale if objective_mode == 'utility' else value
    scaled_bound = (bound/objective_scale if objective_mode == 'utility' else bound) if bound is not None else None
    gap = abs(scaled_bound-scaled_value)/max(1e-9,abs(scaled_value)) if scaled_bound is not None else None
    selected_pois = [p for i,p in enumerate(problem.data.pois) if any(i+1 in r for r in routes)]
    breakdown = {'transport':problem.transport.quote.total_rub,'stay':problem.stay.quote.total_rub,
        'food':problem.request.days*problem.data.daily_costs.food_per_person_day_rub.typical,
        'transfers':2*problem.data.daily_costs.transfer_one_way_rub.typical,
        'admissions':sum(p.admission_rub for p in selected_pois)}
    fingerprint = hashlib.sha256(repr((problem.data.destination.id,problem.transport.quote.id,
                                     problem.stay.quote.id,routes)).encode()).hexdigest()[:16]
    plan = CandidatePlan(id=fingerprint,destination_id=problem.data.destination.id,destination_name=problem.data.destination.name,
        mode=problem.data.mode,transport_id=problem.transport.quote.id,stay_id=problem.stay.quote.id,
        days=[simulate_day(problem,route,d) for d,route in enumerate(routes)],criteria=criteria,
        score=utility(criteria,request.weights),nominal_cost_rub=validation['nominal_cost_rub'],
        expected_cost_rub=float(validation['costs'].mean()),cost_breakdown=breakdown,
        solver=SolverInfo(status=final_status,objective=scaled_value,best_bound=scaled_bound,relative_gap=gap,
                          elapsed_seconds=elapsed,time_limit_seconds=time_limit,objective_mode=objective_mode,
                          scenario_constraints=len(active_scenarios)),planning_success_rate=validation['success_rate'],
        scenario_seed=problem.scenarios.seed)
    return {'plan':plan,'status':final_status,'elapsed_seconds':elapsed}
