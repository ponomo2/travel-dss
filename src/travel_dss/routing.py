from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil, floor
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import Field

from .catalog_api import StayOffer, TransportOffer, TripDataset
from .domain import Record, TripRequest
from .preferences import Criteria, evaluate_criteria, utility, weather_comfort
from .scenarios import ScenarioSet


class Visit(Record):
    poi_id: str
    name: str
    start: datetime
    end: datetime
    walk_before_m: int
    travel_before_minutes: int
    kind: str = 'visit'


class DayPlan(Record):
    day: date
    start: datetime
    end: datetime
    visits: List[Visit]
    walk_m: int
    return_minutes: int
    return_walk_m: int


class SolverInfo(Record):
    status: str
    objective: Optional[float] = None
    best_bound: Optional[float] = None
    relative_gap: Optional[float] = None
    elapsed_seconds: float
    time_limit_seconds: float
    objective_mode: str = 'utility'
    scenario_constraints: int = 0
    model_note: str = 'Целочисленная полезность; полный каталог; сценарные временные ограничения добавляются по нарушениям'


class CandidatePlan(Record):
    id: str
    destination_id: str
    destination_name: str
    mode: str
    transport_id: str
    stay_id: str
    days: List[DayPlan]
    criteria: Criteria
    score: float
    nominal_cost_rub: float
    expected_cost_rub: float
    cost_breakdown: Dict[str, float]
    solver: SolverInfo
    planning_success_rate: float
    scenario_seed: int
    risk: Optional[dict] = None
    stress_tests: List[dict] = Field(default_factory=list)
    knowledge_trace: Optional[dict] = None
    decision_analysis: Optional[dict] = None

    @property
    def poi_ids(self) -> List[str]:
        return [v.poi_id for day in self.days for v in day.visits if v.kind == 'visit']


@dataclass
class RoutingProblem:
    request: TripRequest
    data: TripDataset
    transport: TransportOffer
    stay: StayOffer
    scenarios: ScenarioSet
    windows: List[List[List[Tuple[int, int]]]]
    distances: List[List[Optional[int]]]
    minutes: List[List[Optional[int]]]
    dates: List[date]
    starts: List[int]
    ends: List[int]
    weather_utility: float
    transfer_hours: float

    @property
    def n(self) -> int:
        return len(self.data.pois)

    @property
    def meal(self) -> int:
        return self.n+1

    def duration(self, node: int) -> int:
        if node == 0:
            return 0
        return self.request.meal_minutes if node == self.meal else self.data.pois[node-1].duration_min

    def day_bounds(self, day: int, scenario: Optional[int] = None) -> Tuple[int, int]:
        start, end = self.starts[day], self.ends[day]
        if scenario is not None:
            transfer = self.scenarios.transfer_minutes[scenario]
            baseline = self.data.daily_costs.transfer_one_way_minutes.typical
            if day == 0:
                arrival = minute_of_day(self.transport.destination_arrival, self.data.destination.timezone)
                start = max(9*60, minute_of_day(self.stay.quote.start, self.data.destination.timezone),
                            arrival+ceil(transfer[0]))
            if day == len(self.dates)-1:
                end += ceil(baseline)-ceil(transfer[1])
        return start, end

    def travel(self, left: int, right: int, day: int, scenario: Optional[int] = None) -> Optional[int]:
        value = self.minutes[left][right]
        if value is None:
            return None
        factor = 1 if scenario is None else self.scenarios.travel_factors[scenario, day]
        return ceil(value*factor)

    def node_windows(self, node: int, day: int) -> List[Tuple[int, int]]:
        return [(12*60, 15*60-self.request.meal_minutes)] if node == self.meal else self.windows[day][node-1]

    def cost_samples(self, routes: List[List[int]]) -> np.ndarray:
        selected = [node-1 for route in routes for node in route if 0 < node <= self.n]
        costs = self.scenarios.base_costs(self.transport.quote.id, self.stay.quote.id).copy()
        if selected:
            costs += self.scenarios.admission_rub[:, selected].sum(axis=1)
        return costs


def minute_of_day(value: datetime, tz: str) -> int:
    local = value.astimezone(ZoneInfo(tz))
    return local.hour*60+local.minute


def make_problem(data: TripDataset, request: TripRequest, transport: TransportOffer,
                 stay: StayOffer, scenarios: ScenarioSet) -> RoutingProblem:
    if scenarios.poi_ids != [p.id for p in data.pois]:
        raise ValueError('Порядок объектов и сценариев не совпадает')
    tz = data.destination.timezone
    dates = [data.window.start+timedelta(days=i) for i in range(request.days)]
    windows = []
    for day in dates:
        midnight = datetime.combine(day,datetime.min.time(),tzinfo=ZoneInfo(tz))
        relative_minutes = lambda instant: floor((instant-midnight).total_seconds()/60)
        windows.append([[(relative_minutes(w.start), min(relative_minutes(w.end)-p.duration_min,
                          relative_minutes(w.last_admission) if w.last_admission else 2880))
                         for w in data.opening_windows(p.id, day)] for p in data.pois])
    points = [stay.location.id]+[p.id for p in data.pois]+[stay.location.id]
    distances, minutes = [], []
    for left in points:
        edges = [data.travel_between(left, right) for right in points]
        distances.append([None if e['distance_m'] is None else ceil(e['distance_m']) for e in edges])
        minutes.append([None if e['duration_seconds'] is None else ceil(e['duration_seconds']/60) for e in edges])
    transfer = data.daily_costs.transfer_one_way_minutes.typical
    starts, ends = [9*60]*len(dates), [20*60]*len(dates)
    starts[0] = max(starts[0], minute_of_day(transport.destination_arrival, tz)+ceil(transfer),
                    minute_of_day(stay.quote.start, tz))
    ends[-1] = min(ends[-1], minute_of_day(transport.return_departure, tz)-ceil(transfer)-request.event_buffer_minutes)
    intercity_hours = ((transport.destination_arrival-transport.quote.start).total_seconds()
                       +(transport.quote.end-transport.return_departure).total_seconds())/3600
    return RoutingProblem(request, data, transport, stay, scenarios, windows, distances, minutes, dates,
                          starts, ends, weather_comfort(scenarios.weather, request),
                          intercity_hours+float(scenarios.transfer_minutes.mean(axis=0).sum())/60)


def simulate_day(problem: RoutingProblem, route: List[int], day: int,
                 scenario: Optional[int] = None) -> Optional[DayPlan]:
    start, end_limit = problem.day_bounds(day, scenario)
    current, previous, walked = start, 0, 0
    visits = []
    tz = ZoneInfo(problem.data.destination.timezone)
    midnight = datetime.combine(problem.dates[day], datetime.min.time(), tzinfo=tz)
    for node in route:
        if not 1 <= node <= problem.meal:
            return None
        travel = problem.travel(previous, node, day, scenario)
        distance = problem.distances[previous][node]
        if travel is None or distance is None:
            return None
        arrival = current+travel
        feasible = [max(arrival, opening) for opening, latest in problem.node_windows(node, day)
                    if max(arrival, opening) <= latest]
        if not feasible:
            return None
        visit_start = min(feasible)
        current = visit_start+problem.duration(node)
        walked += distance
        meal = node == problem.meal
        visits.append(Visit(poi_id='__meal__' if meal else problem.data.pois[node-1].id,
            name='Обед около базы маршрута' if meal else problem.data.pois[node-1].name,
            start=midnight+timedelta(minutes=visit_start), end=midnight+timedelta(minutes=current),
            walk_before_m=distance, travel_before_minutes=travel, kind='meal' if meal else 'visit'))
        previous = node
    travel = problem.travel(previous, 0, day, scenario)
    distance = problem.distances[previous][0]
    if travel is None or distance is None:
        return None
    current += travel
    walked += distance
    if (current > end_limit or current-start > problem.request.active_minutes_day
            or walked > problem.request.max_walk_km_day*1000 or route.count(problem.meal) != 1):
        return None
    return DayPlan(day=problem.dates[day], start=midnight+timedelta(minutes=start),
                   end=midnight+timedelta(minutes=current), visits=visits, walk_m=walked,
                   return_minutes=travel, return_walk_m=distance)


def check_routes(problem: RoutingProblem, routes: List[List[int]], scenario: Optional[int] = None) -> bool:
    if len(routes) != len(problem.dates):
        return False
    selected = [node for route in routes for node in route if node != problem.meal]
    if not selected or len(selected) != len(set(selected)) or any(not 1 <= node <= problem.n for node in selected):
        return False
    poi_ids = {problem.data.pois[node-1].id for node in selected}
    if not set(problem.request.required_poi_ids) <= poi_ids or poi_ids & set(problem.request.excluded_poi_ids):
        return False
    return all(simulate_day(problem, route, day, scenario) is not None for day, route in enumerate(routes))


def route_criteria(problem: RoutingProblem, routes: List[List[int]]) -> Criteria:
    selected = [problem.data.pois[node-1] for route in routes for node in route if node != problem.meal]
    days = [simulate_day(problem, route, day) for day, route in enumerate(routes)]
    if any(day is None for day in days):
        raise ValueError('Маршрут не прошел независимую проверку')
    return evaluate_criteria(selected, problem.request, float(problem.cost_samples(routes).mean()),
                             problem.transfer_hours, sum(day.walk_m for day in days)/1000, problem.weather_utility)


def validate_plan(problem: RoutingProblem, routes: List[List[int]]) -> Dict[str, object]:
    nominal = problem.data.estimate_cost(problem.transport.quote.id, problem.stay.quote.id,
        [problem.data.pois[node-1].id for route in routes for node in route if node != problem.meal]).typical
    costs = problem.cost_samples(routes)
    nominal_ok = nominal <= problem.request.budget_rub and check_routes(problem, routes)
    success = scenario_success(problem,routes) if nominal_ok else np.zeros(problem.scenarios.count,dtype=bool)
    return {'nominal_valid': nominal_ok, 'success': success, 'costs': costs,
            'success_rate': float(success.mean()), 'nominal_cost_rub': nominal}


def scenario_success(problem: RoutingProblem, routes: List[List[int]]) -> np.ndarray:
    costs = problem.cost_samples(routes)
    success = costs <= problem.request.budget_rub
    s = problem.scenarios
    for day, route in enumerate(routes):
        start = np.full(s.count,problem.starts[day],dtype=float)
        end = np.full(s.count,problem.ends[day],dtype=float)
        if day == 0:
            arrival = minute_of_day(problem.transport.destination_arrival,problem.data.destination.timezone)
            checkin = minute_of_day(problem.stay.quote.start,problem.data.destination.timezone)
            start = np.maximum(max(9*60,checkin),arrival+np.ceil(s.transfer_minutes[:,0]))
        if day == len(routes)-1:
            end += ceil(problem.data.daily_costs.transfer_one_way_minutes.typical)-np.ceil(s.transfer_minutes[:,1])
        current, previous = start.copy(),0
        walked = 0
        for node in route:
            minutes = problem.minutes[previous][node]
            distance = problem.distances[previous][node]
            if minutes is None or distance is None:
                return np.zeros(s.count,dtype=bool)
            arrival = current+np.ceil(minutes*s.travel_factors[:,day])
            options = [np.where(np.maximum(arrival,opening) <= latest,np.maximum(arrival,opening),np.inf)
                       for opening,latest in problem.node_windows(node,day)]
            current = np.minimum.reduce(options)+problem.duration(node) if options else np.full(s.count,np.inf)
            walked += distance
            previous = node
        minutes, distance = problem.minutes[previous][0], problem.distances[previous][0]
        if minutes is None or distance is None:
            return np.zeros(s.count,dtype=bool)
        current += np.ceil(minutes*s.travel_factors[:,day])
        walked += distance
        success &= ((current <= end) & (current-start <= problem.request.active_minutes_day)
                    & (walked <= problem.request.max_walk_km_day*1000) & (route.count(problem.meal) == 1))
    return success


def candidate_from_routes(problem: RoutingProblem, routes: List[List[int]], solver: SolverInfo) -> CandidatePlan:
    import hashlib
    checked = validate_plan(problem,routes)
    if not checked['nominal_valid'] or checked['success_rate']+1e-9 < problem.request.min_success_rate:
        raise ValueError('Нельзя публиковать непроверенный маршрут')
    criteria = route_criteria(problem,routes)
    selected = [problem.data.pois[i-1] for route in routes for i in route if i != problem.meal]
    fingerprint = hashlib.sha256(repr((problem.data.destination.id,problem.transport.quote.id,problem.stay.quote.id,routes)).encode()).hexdigest()[:16]
    return CandidatePlan(id=fingerprint,destination_id=problem.data.destination.id,destination_name=problem.data.destination.name,
        mode=problem.data.mode,transport_id=problem.transport.quote.id,stay_id=problem.stay.quote.id,
        days=[simulate_day(problem,route,d) for d,route in enumerate(routes)],criteria=criteria,
        score=utility(criteria,problem.request.weights),nominal_cost_rub=checked['nominal_cost_rub'],
        expected_cost_rub=float(checked['costs'].mean()),cost_breakdown={
            'transport':problem.transport.quote.total_rub,'stay':problem.stay.quote.total_rub,
            'food':problem.request.days*problem.data.daily_costs.food_per_person_day_rub.typical,
            'transfers':2*problem.data.daily_costs.transfer_one_way_rub.typical,
            'admissions':sum(p.admission_rub for p in selected)},solver=solver,
        planning_success_rate=checked['success_rate'],scenario_seed=problem.scenarios.seed)
