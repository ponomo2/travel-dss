from math import ceil, floor
from time import monotonic

import numpy as np
import pulp

from .preferences import utility
from .routing import route_criteria, validate_plan, candidate_from_routes, SolverInfo


def solve_pulp(problem, time_limit: float = 30, output_prefix=None, objective_mode='utility', min_utility=None, excluded_sets=None) -> dict:
    started = monotonic()
    model = pulp.LpProblem('TravelDSS',pulp.LpMaximize)
    request = problem.request
    n,meal = problem.n,problem.meal
    days = range(len(problem.dates))
    y = pulp.LpVariable.dicts('visit',[(d,i) for d in days for i in range(1,meal+1)],cat='Binary')
    x = pulp.LpVariable.dicts('arc',[(d,i,j) for d in days for i in range(meal+1) for j in range(meal+1)
                                   if i!=j and problem.minutes[i][j] is not None],cat='Binary')
    selected = {i:pulp.lpSum(y[d,i+1] for d in days) for i in range(n)}
    for i,poi in enumerate(problem.data.pois):
        model += selected[i] <= 1
        if poi.id in request.required_poi_ids: model += selected[i] == 1
        if poi.id in request.excluded_poi_ids: model += selected[i] == 0
    model += pulp.lpSum(selected.values()) >= 1
    for d in days:
        for i in range(meal+1):
            outgoing = pulp.lpSum(v for (day,left,right),v in x.items() if day==d and left==i)
            incoming = pulp.lpSum(v for (day,left,right),v in x.items() if day==d and right==i)
            model += outgoing == (1 if i==0 else y[d,i])
            model += incoming == (1 if i==0 else y[d,i])
        model += y[d,meal] == 1
        model += pulp.lpSum(problem.distances[i][j]*v for (day,i,j),v in x.items() if day==d) <= request.max_walk_km_day*1000
    nominal = problem.data.estimate_cost(problem.transport.quote.id,problem.stay.quote.id,[]).typical
    model += nominal+pulp.lpSum(p.admission_rub*selected[i] for i,p in enumerate(problem.data.pois)) <= request.budget_rub
    samples = problem.scenarios
    bases = samples.base_costs(problem.transport.quote.id,problem.stay.quote.id)
    rejected = pulp.LpVariable.dicts('scenario_failure',range(samples.count),cat='Binary')
    model += pulp.lpSum(rejected.values()) <= floor((1-request.min_success_rate)*samples.count+1e-8)
    for s in range(samples.count):
        upper = float(bases[s]+samples.admission_rub[s].sum())
        model += bases[s]+pulp.lpSum(float(samples.admission_rub[s,i])*selected[i] for i in range(n)) <= request.budget_rub+max(1,upper)*rejected[s]
    def add_schedule(scenario=None):
        tag = 'nominal' if scenario is None else str(scenario)
        relax = 0 if scenario is None else rejected[scenario]
        for d in days:
            start,end = problem.day_bounds(d,scenario)
            times = {i:pulp.LpVariable(f't_{tag}_{d}_{i}',0,2880) for i in range(1,meal+1)}
            order = {i:pulp.LpVariable(f'u_{tag}_{d}_{i}',1,meal) for i in range(1,meal+1)}
            for i in range(1,meal+1):
                windows = []
                for k,(low,high) in enumerate(problem.node_windows(i,d)):
                    window = pulp.LpVariable(f'window_{tag}_{d}_{i}_{k}',cat='Binary')
                    windows.append(window)
                    model.addConstraint(times[i] >= low-5760*(1-window+relax))
                    model.addConstraint(times[i] <= high+5760*(1-window+relax))
                model.addConstraint(pulp.lpSum(windows) == y[d,i])
            for (day,i,j),arc in x.items():
                if day != d: continue
                duration = problem.duration(i)
                travel = problem.travel(i,j,d,scenario)
                if i==0:
                    model.addConstraint(times[j] >= start+travel-5760*(1-arc+relax))
                elif j==0:
                    model.addConstraint(times[i]+duration+travel <= min(end,start+request.active_minutes_day)+5760*(1-arc+relax))
                else:
                    model.addConstraint(times[j] >= times[i]+duration+travel-5760*(1-arc+relax))
                    model.addConstraint(order[j] >= order[i]+1-(meal+1)*(1-arc+relax))
    add_schedule()
    interests = []
    for theme,weight in request.interests.model_dump().items():
        value = pulp.LpVariable('saturation_'+theme,0,1)
        model += value <= pulp.lpSum(p.themes.model_dump()[theme]*selected[i] for i,p in enumerate(problem.data.pois))/request.days
        interests.append(weight*value)
    expected = float(bases.mean())+pulp.lpSum(float(samples.admission_rub[:,i].mean())*selected[i] for i in range(n))
    good = request.good_cost_rub if request.good_cost_rub is not None else .6*request.budget_rub
    maximum = max(request.budget_rub,float(bases.mean()+samples.admission_rub.mean(axis=0).sum()))+1
    positive = pulp.LpVariable('positive_budget_slack',0,maximum)
    sign = pulp.LpVariable('positive_budget_slack_sign',cat='Binary')
    model += positive >= request.budget_rub-expected
    model += positive <= request.budget_rub-expected+maximum*(1-sign)
    model += positive <= maximum*sign
    economy = pulp.LpVariable('economy',0,1)
    model += economy*(request.budget_rub-good) <= positive
    walking = pulp.lpSum(problem.distances[i][j]*v for (d,i,j),v in x.items())
    convenience = 1-.5*min(1,problem.transfer_hours/24)-.5*walking/(request.days*request.max_walk_km_day*1000)
    score_expression = request.weights.interests*pulp.lpSum(interests)+request.weights.economy*economy+request.weights.weather*problem.weather_utility+request.weights.convenience*convenience
    if min_utility is not None: model += score_expression >= min_utility
    if objective_mode == 'utility': model += score_expression
    elif objective_mode in ('cost','convenience'):
        model.sense = pulp.LpMinimize
        model += expected if objective_mode == 'cost' else walking
    else: raise ValueError('Неизвестная цель PuLP')
    for previous in excluded_sets or []:
        model += pulp.lpSum(selected[i] for i,p in enumerate(problem.data.pois) if p.id in previous) <= floor(.7*len(previous))
    active = set()
    routes = None
    status = 'Not Solved'
    validation = None
    while monotonic()-started < time_limit:
        solver = pulp.PULP_CBC_CMD(msg=False,timeLimit=max(.01,time_limit-(monotonic()-started)),threads=1)
        model.solve(solver)
        status = pulp.LpStatus[model.status]
        if model.sol_status not in (pulp.LpSolutionOptimal,pulp.LpSolutionIntegerFeasible):
            break
        proposed = []
        for d in days:
            current,route = 0,[]
            for _ in range(meal+1):
                following = [j for (day,i,j),v in x.items() if day==d and i==current and pulp.value(v)>.5]
                if len(following)!=1: break
                current = following[0]
                if current==0: break
                route.append(current)
            proposed.append(route)
        validation = validate_plan(problem,proposed)
        if validation['nominal_valid'] and validation['success_rate']+1e-9 >= request.min_success_rate:
            routes = proposed
            break
        violations = [s for s,ok in enumerate(validation['success']) if not ok and s not in active]
        if not violations: break
        for s in violations:
            add_schedule(s)
            active.add(s)
    if output_prefix:
        model.writeLP(str(output_prefix)+'.lp')
        model.writeMPS(str(output_prefix)+'.mps')
    if routes is None and not excluded_sets:
        from .baselines import greedy_routes
        seed_routes = greedy_routes(problem)
        if seed_routes is not None and (min_utility is None or utility(route_criteria(problem,seed_routes),request.weights)>=min_utility):
            routes = seed_routes
            validation = validate_plan(problem,routes)
            model.sol_status = pulp.LpSolutionIntegerFeasible
    if routes is None:
        return {'status':'INFEASIBLE' if status=='Infeasible' else 'UNKNOWN','routes':None,'plan':None,'elapsed_seconds':monotonic()-started,'validated':False}
    criteria = route_criteria(problem,routes)
    optimal = model.sol_status==pulp.LpSolutionOptimal
    elapsed = monotonic()-started
    status = 'OPTIMAL' if optimal else 'FEASIBLE'
    info = SolverInfo(status=status,elapsed_seconds=elapsed,time_limit_seconds=time_limit,objective_mode=objective_mode,
        objective=float(pulp.value(model.objective)) if optimal else None,model_note='PuLP MILP / CBC; полная независимая проверка сценариев')
    plan = candidate_from_routes(problem,routes,info)
    return {'status':status,'plan':plan,'routes':routes,'validated':True,
        'score':utility(criteria,request.weights),'criteria':criteria.model_dump(),
        'objective':info.objective,'nominal_cost_rub':validation['nominal_cost_rub'],
        'planning_success_rate':validation['success_rate'],'elapsed_seconds':monotonic()-started,
        'scenario_constraints':len(active),'backend':'PuLP 2.9 / CBC'}
