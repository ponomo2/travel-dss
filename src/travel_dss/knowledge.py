import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from pydantic import Field, model_validator

from .data import ROOT
from .domain import Record


class Condition(Record):
    predicate: str
    operator: str
    value: Any

    @model_validator(mode='after')
    def validate_operator(self):
        if self.operator not in ('eq','gt','gte','lt','lte'):
            raise ValueError('Неизвестный оператор правила')
        return self

    def matches(self,facts: dict) -> bool:
        if self.predicate not in facts:
            return False
        current = facts[self.predicate]
        if self.operator == 'eq':
            return type(current) is type(self.value) and current == self.value
        if type(current) not in (int,float) or type(self.value) not in (int,float):
            return False
        return {'gt':lambda:current>self.value,'gte':lambda:current>=self.value,
                'lt':lambda:current<self.value,'lte':lambda:current<=self.value}[self.operator]()


class Rule(Record):
    id: str
    all: List[Condition] = Field(min_length=1)
    then: Dict[str,Any]
    explanation: str

    @model_validator(mode='after')
    def validate_conclusion(self):
        if set(self.then) != {'predicate','value'} or not isinstance(self.then['predicate'],str):
            raise ValueError('Неверное заключение правила')
        return self


class KnowledgeBase:
    def __init__(self,path: Path = ROOT/'data/knowledge/rules.json'):
        self.path = Path(path)
        payload = json.loads(self.path.read_text())
        self.version = payload['version']
        self.rules = [Rule.model_validate(r) for r in payload['rules']]
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError('Повторные идентификаторы правил')

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def infer(self,initial: dict) -> dict:
        facts = dict(initial)
        trace = []
        fired = set()
        changed = True
        while changed:
            changed = False
            for rule in self.rules:
                if rule.id in fired or not all(c.matches(facts) for c in rule.all):
                    continue
                predicate,value = rule.then['predicate'],rule.then['value']
                if predicate in facts and (type(facts[predicate]) is not type(value) or facts[predicate] != value):
                    raise ValueError(f'Противоречие вывода: {rule.id} / {predicate}')
                changed |= predicate not in facts
                facts[predicate] = value
                fired.add(rule.id)
                trace.append({'rule_id':rule.id,'premises':{c.predicate:facts[c.predicate] for c in rule.all},
                              'conclusion':dict(rule.then),'explanation':rule.explanation})
        return {'facts':facts,'trace':trace,'knowledge_version':self.version}


def derive_poi_knowledge(data,request,allow_demo: bool = False,rainy_day_share=None) -> dict:
    base = KnowledgeBase()
    reports = {}
    for poi in data.pois:
        facts = {'subject':poi.id,'synthetic':poi.provenance.data_kind.value=='synthetic',
            'data_usable':bool(poi.usable(data.window.start,allow_demo) and poi.usable(data.window.end,allow_demo)),
            'open_during_trip':any(w.poi_id==poi.id for w in data.opening_intervals),
            'excluded':poi.id in request.excluded_poi_ids,'required':poi.id in request.required_poi_ids}
        if poi.indoor is not None:
            facts['outdoor'] = not poi.indoor
        if rainy_day_share is not None:
            facts['rainy_day_share'] = float(rainy_day_share)
        reports[poi.id] = base.infer(facts)
    return {'version':base.version,'sha256':base.sha256,'entities':reports}
