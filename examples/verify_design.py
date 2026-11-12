"""Synthetic arithmetic checks for the design; not the travel DSS implementation."""
from itertools import permutations
import json
from pathlib import Path


def main():
    features = {
        "Казань": [0.90, 0.50, 0.60, 0.70],
        "Нижний Новгород": [0.80, 0.75, 0.65, 0.80],
        "Сочи": [0.70, 0.65, 0.90, 0.60],
    }
    profiles = {"сбалансированный": [0.45, 0.25, 0.15, 0.15],
                "интересы прежде всего": [0.70, 0.10, 0.10, 0.10]}
    scores = {profile: {city: round(sum(w*x for w, x in zip(weights, row)), 4)
                        for city, row in features.items()}
              for profile, weights in profiles.items()}
    assert scores["сбалансированный"]["Нижний Новгород"] == 0.765
    assert scores["интересы прежде всего"]["Казань"] == 0.81

    # position, visit minutes, admission RUB, utility; depot is at position 0.
    pois = {"A": (1, 120, 1200, 9), "B": (2, 90, 800, 8),
            "C": (3, 60, 0, 6), "D": (10, 90, 400, 10)}

    def evaluate(route):
        positions = [0] + [pois[i][0] for i in route] + [0]
        travel = sum(10 * abs(a-b) for a, b in zip(positions, positions[1:]))
        minutes = travel + sum(pois[i][1] for i in route) + 30  # meal
        cost = sum(pois[i][2] for i in route)
        utility = sum(pois[i][3] for i in route)
        return {"route": list(route), "minutes": minutes, "cost": cost,
                "utility": utility, "feasible": minutes <= 330 and cost <= 2000}

    candidates = [evaluate(p) for n in range(1, 5) for p in permutations(pois, n)]
    best = max((r for r in candidates if r["feasible"]), key=lambda r: r["utility"])
    greedy = []
    for poi in sorted(pois, key=lambda p: pois[p][3], reverse=True):
        if evaluate(greedy + [poi])["feasible"]:
            greedy.append(poi)
    greedy_result = evaluate(greedy)
    assert best["utility"] == 17 and best["minutes"] == 280
    assert greedy_result["utility"] == 10
    result = {
        "notice": "Все данные синтетические; это проверка примеров, не оценка MVP.",
        "ranking": scores,
        "route": {"enumerated": len(candidates), "best": best,
                  "greedy_by_individual_utility": greedy_result,
                  "utility_gain_percent": round(100*(17/10-1), 1)},
    }
    output = Path(__file__).with_name("design_demo_results.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
