from copy import deepcopy

from .routing import RoutingProblem, simulate_day, validate_plan


def greedy_routes(problem: RoutingProblem, method: str = 'utility', robust: bool = True):
    routes = [[problem.meal] for _ in problem.dates]
    if any(simulate_day(problem,route,d) is None for d,route in enumerate(routes)):
        return None
    weights = problem.request.interests.model_dump()
    priorities = []
    for i,p in enumerate(problem.data.pois):
        interest = sum(weights[k]*value for k,value in p.themes.model_dump().items())
        distance = problem.distances[0][i+1]
        if distance is None:
            continue
        priority = distance if method == 'nearest' else -interest/max(1,p.duration_min)
        priorities.append((p.id not in problem.request.required_poi_ids,priority,i+1))
    accepted = []
    for _,_,node in sorted(priorities):
        possibilities = []
        for d,route in enumerate(routes):
            for position in range(len(route)+1):
                trial = route[:position]+[node]+route[position:]
                day = simulate_day(problem,trial,d)
                if day is not None:
                    possibilities.append((day.walk_m,d,trial))
        for _,d,trial in sorted(possibilities,key=lambda x:x[0]):
            proposal = deepcopy(routes)
            proposal[d] = trial
            selected = [problem.data.pois[i-1].id for r in proposal for i in r if i != problem.meal]
            cost = problem.data.estimate_cost(problem.transport.quote.id,problem.stay.quote.id,selected).typical
            if cost <= problem.request.budget_rub:
                routes = proposal
                accepted.append(node)
                break
    while True:
        validation = validate_plan(problem,routes)
        if validation['nominal_valid'] and (not robust or validation['success_rate']+1e-9 >= problem.request.min_success_rate):
            return routes
        removable = [node for node in reversed(accepted) if problem.data.pois[node-1].id not in problem.request.required_poi_ids]
        if not removable:
            return None
        node = removable[0]
        accepted.remove(node)
        routes = [[i for i in route if i != node] for route in routes]
