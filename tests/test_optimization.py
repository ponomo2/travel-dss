from itertools import permutations

import numpy as np
import pytest

from conftest import small_problem
from travel_dss.optimization import solve_itinerary
from travel_dss.preferences import utility
from travel_dss.routing import check_routes,route_criteria,scenario_success,validate_plan


def exhaustive(problem):
    best = -1
    for length in range(problem.n+1):
        for subset in permutations(range(1,problem.n+1),length):
            for position in range(length+1):
                route = list(subset[:position])+[problem.meal]+list(subset[position:])
                if check_routes(problem,[route]):
                    validation = validate_plan(problem,[route])
                    if validation['nominal_valid']:
                        best = max(best,utility(route_criteria(problem,[route]),problem.request.weights))
    return best


@pytest.mark.parametrize('seed',range(20))
def test_matches_exhaustive_small_tasks(trip_data,seed):
    request,data = trip_data
    problem = small_problem(request,data,seed)
    expected = exhaustive(problem)
    result = solve_itinerary(problem,3)
    assert result['plan'] is not None
    plan = result['plan']
    assert plan.solver.status == 'OPTIMAL'
    assert plan.score == pytest.approx(expected,abs=.001)
    assert abs(plan.score-plan.solver.objective) < .002
    assert plan.solver.relative_gap == pytest.approx(0)


def test_budget_impossible_and_required_constraint(trip_data):
    request,data = trip_data
    problem = small_problem(request,data,1)
    problem.request = problem.request.model_copy(update={'budget_rub':1000,'good_cost_rub':500})
    assert solve_itinerary(problem,1)['status'] == 'INFEASIBLE'
    problem = small_problem(request,data,1)
    problem.request = problem.request.model_copy(update={'required_poi_ids':[data.pois[0].id]})
    problem.windows[0][0] = []
    assert solve_itinerary(problem,1)['plan'] is None


def test_vectorized_check_matches_scalar(trip_data):
    from travel_dss.baselines import greedy_routes
    from travel_dss.routing import make_problem
    from travel_dss.scenarios import build_scenarios
    request,data = trip_data
    problem = make_problem(data,request,data.transport_offers[0],data.stay_offers[0],build_scenarios(data,100,77))
    routes = greedy_routes(problem)
    assert routes is not None
    scalar = np.array([problem.cost_samples(routes)[s] <= request.budget_rub and check_routes(problem,routes,s) for s in range(100)])
    np.testing.assert_array_equal(scenario_success(problem,routes),scalar)


def test_duplicate_and_missing_meal_are_rejected(trip_data):
    problem = small_problem(*trip_data,seed=1)
    assert not check_routes(problem,[[1,1,problem.meal]])
    assert not check_routes(problem,[[1]])


def test_overnight_opening_interval_is_not_truncated(trip_data):
    from datetime import timedelta
    from travel_dss.domain import OpeningInterval
    from travel_dss.routing import make_problem
    from travel_dss.scenarios import build_scenarios
    request,data = trip_data
    poi = data.pois[0]
    existing = next(w for w in data.opening_intervals if w.poi_id == poi.id)
    midnight = existing.start.replace(hour=0,minute=0)
    overnight = OpeningInterval(poi_id=poi.id,start=midnight.replace(hour=9),end=midnight+timedelta(days=1,hours=1),
                                last_admission=midnight+timedelta(days=1),provenance=existing.provenance)
    changed = data.model_copy(update={'opening_intervals':[overnight]+[w for w in data.opening_intervals if w.poi_id != poi.id]})
    problem = make_problem(changed,request,changed.transport_offers[0],changed.stay_offers[0],build_scenarios(changed,10))
    assert problem.windows[0][0][0][1] >= 24*60
