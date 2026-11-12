from math import sqrt

import numpy as np

from .routing import CandidatePlan, RoutingProblem, check_routes, scenario_success


def wilson_interval(successes: int, count: int, z: float = 1.959963984540054):
    if count < 1 or not 0 <= successes <= count:
        raise ValueError('Некорректная выборка для интервала')
    p = successes/count
    denominator = 1+z*z/count
    center = (p+z*z/(2*count))/denominator
    half = z*sqrt(p*(1-p)/count+z*z/(4*count**2))/denominator
    return max(0,center-half),min(1,center+half)


def plan_routes(plan: CandidatePlan, problem: RoutingProblem):
    index = {p.id:i+1 for i,p in enumerate(problem.data.pois)}
    return [[problem.meal if visit.kind == 'meal' else index[visit.poi_id] for visit in day.visits] for day in plan.days]


def assess_risk(plan: CandidatePlan, problem: RoutingProblem) -> dict:
    routes = plan_routes(plan,problem)
    if not check_routes(problem,routes):
        raise ValueError('План не прошел независимую проверку расписания')
    costs = problem.cost_samples(routes)
    success = scenario_success(problem,routes)
    low,high = wilson_interval(int(success.sum()),len(success))
    return {'sample_count':len(success),'seed':problem.scenarios.seed,'success_rate':float(success.mean()),
        'wilson_lower':low,'wilson_upper':high,'required_rate':problem.request.min_success_rate,
        'robust_by_model':bool(low >= problem.request.min_success_rate),
        'cost_c50_rub':float(np.quantile(costs,.5)),'cost_c90_rub':float(np.quantile(costs,.9)),
        'budget_failure_rate':float((costs > problem.request.budget_rub).mean()),
        'interpretation':'Доля совместно успешных сценариев при принятых предположениях; закрытия проверяются отдельно',
        'independent_from_planning':plan.scenario_seed != problem.scenarios.seed}
