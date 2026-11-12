from typing import List

import numpy as np
from pydantic import Field

from .domain import Record, Weights


CRITERIA = list(Weights.model_fields)
DEMO_MATRIX = [[1,2,3,3],[.5,1,2,2],[1/3,.5,1,1],[1/3,.5,1,1]]
RANDOM_INDEX = {1:0,2:0,3:.58,4:.90,5:1.12,6:1.24,7:1.32,8:1.41,9:1.45,10:1.49}


class AHPResult(Record):
    matrix: List[List[float]]
    priorities: List[float]
    lambda_max: float
    consistency_index: float
    random_index: float
    consistency_ratio: float
    acceptable: bool

    def as_weights(self) -> Weights:
        if len(self.priorities) != 4 or not self.acceptable:
            raise ValueError('Нужна согласованная матрица четырех критериев (CR ≤ 0.10)')
        return Weights(**dict(zip(CRITERIA,self.priorities)))


def analyze_ahp(matrix, threshold: float = .10) -> AHPResult:
    values = np.asarray(matrix,dtype=float)
    if values.ndim != 2 or values.shape[0] != values.shape[1] or not 1 <= len(values) <= 10:
        raise ValueError('Матрица AHP должна быть квадратной размером 1–10')
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('Сравнения должны быть конечными положительными числами')
    if not np.allclose(np.diag(values),1,atol=1e-10) or not np.allclose(values*values.T,1,atol=1e-8):
        raise ValueError('Нужны единицы на диагонали и взаимно обратные сравнения')
    if not 0 <= threshold <= 1:
        raise ValueError('Некорректный порог согласованности')
    priorities = np.full(len(values),1/len(values))
    for _ in range(10000):
        updated = np.einsum('ij,j->i',values,priorities)
        updated /= updated.sum()
        if np.max(abs(updated-priorities)) < 1e-13:
            priorities = updated
            break
        priorities = updated
    else:
        raise ValueError('Вычисление приоритетов не сошлось')
    maximum = float(np.mean(np.einsum('ij,j->i',values,priorities)/priorities))
    ci = max(0,(maximum-len(values))/(len(values)-1)) if len(values)>1 else 0
    ri = RANDOM_INDEX[len(values)]
    cr = ci/ri if ri else 0
    return AHPResult(matrix=values.tolist(),priorities=priorities.tolist(),lambda_max=maximum,
                     consistency_index=ci,random_index=ri,consistency_ratio=cr,acceptable=cr<=threshold)


def aggregate_alternatives(criteria, weights: Weights) -> dict:
    names = list(criteria)
    local = {}
    for criterion in CRITERIA:
        values = np.array([getattr(criteria[name],criterion) for name in names])
        if (values <= 0).any():
            raise ValueError('Для AHP ratio-синтеза нужны положительные локальные полезности')
        result = analyze_ahp(values[:,None]/values[None,:])
        local[criterion] = result
    scores = {name:sum(weights.model_dump()[key]*local[key].priorities[i] for key in CRITERIA)
              for i,name in enumerate(names)}
    return {'scores':scores,'ranking':sorted(names,key=lambda name:(-scores[name],name)),
            'local_matrices':{key:result.model_dump() for key,result in local.items()},
            'note':'Локальные матрицы отношений рассчитанных положительных полезностей; CR=0 по построению, не независимая экспертиза'}
