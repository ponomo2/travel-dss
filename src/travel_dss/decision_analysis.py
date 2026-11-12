from typing import Optional

import networkx as nx
from scipy.optimize import brentq


def analyze_closure_strategy(plan, preparation_penalty: float = .01, failure_utility: float = -.2,
                             closure_probability: Optional[float] = None) -> dict:
    if preparation_penalty < 0 or failure_utility > 0:
        raise ValueError('Некорректные последствия стратегии')
    if closure_probability is not None and not 0 <= closure_probability <= 1:
        raise ValueError('Вероятность должна быть в диапазоне 0–1')
    available = next((s for s in plan.stress_tests if s.get('status')=='BACKUP_VALIDATED'),None)
    if plan.risk is None or available is None:
        return {'status':'no_validated_backup','message':'Для сравнения стратегий нужен заново проверенный резерв'}
    backup = available['backup']
    main_rate = plan.risk['success_rate']
    backup_rate = backup['risk']['success_rate']
    main_payoff = main_rate*plan.score+(1-main_rate)*failure_utility
    backup_payoff = backup_rate*backup['score']+(1-backup_rate)*failure_utility
    graph = nx.DiGraph()
    graph.add_node('decision',kind='decision',label='Подготовка резерва')
    for strategy in ('fixed','reserve'):
        graph.add_node(strategy,kind='chance',label='Закрытие выбранного объекта')
        graph.add_edge('decision',strategy,choice='Сохранить исходный план' if strategy=='fixed' else 'Подготовить резерв')
        for state,payoff in [('open',main_payoff),('closed',failure_utility if strategy=='fixed' else backup_payoff)]:
            node = strategy+'_'+state
            graph.add_node(node,kind='terminal',expected_utility=payoff-(preparation_penalty if strategy=='reserve' else 0),
                           scenario_success_rate=main_rate if state=='open' else 0 if strategy=='fixed' else backup_rate)
            graph.add_edge(strategy,node,state=state,probability='1-p' if state=='open' else 'p')
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError('Дерево решений содержит цикл')
    def difference(p):
        return p*(backup_payoff-failure_utility)-preparation_penalty
    threshold = None
    if difference(0)*difference(1) <= 0:
        threshold = float(brentq(difference,0,1))
    rows = []
    probabilities = [0,.01,.05,.10,.20] if closure_probability is None else [closure_probability]
    for p in probabilities:
        fixed = (1-p)*main_payoff+p*failure_utility
        reserve = (1-p)*main_payoff+p*backup_payoff-preparation_penalty
        rows.append({'closure_probability_assumption':p,'fixed_expected_utility':fixed,
                     'reserve_expected_utility':reserve,'preferred':'reserve' if reserve>fixed else 'fixed'})
    return {'status':'evaluated','closed_poi_id':available['closed_poi_id'],
        'failure_utility_assumption':failure_utility,'preparation_penalty_assumption':preparation_penalty,
        'main_expected_utility_without_closure':main_payoff,'backup_expected_utility_when_closed':backup_payoff,
        'break_even_probability':threshold,'sensitivity':rows,
        'closure_probability_source':'not estimated; user/scenario assumption',
        'tree':{'nodes':[dict(id=n,**a) for n,a in graph.nodes(data=True)],
                'edges':[dict(source=a,target=b,**v) for a,b,v in graph.edges(data=True)]},
        'interpretation':'Порог показывает, при какой субъективной вероятности закрытия подготовка проверенного резерва окупает заданную потерю полезности; частота закрытий не измерена'}
