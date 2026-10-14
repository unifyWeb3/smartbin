"""Check that the dashboard shows the same numbers as the experiment.

The dashboard must not be a second, slightly different simulation. This
drives it exactly the way the buttons do - build both models on one scenario,
step them to the end - and compares every metric with the row that
`run_experiment.py` saved in results.csv for the same seed, load and
strategy.

    python test_dashboard.py
"""

import pandas as pd

import config as C
from scenario import city_arrival_rate, generate_scenario, generate_scenarios

SEED = 1000
LEVEL = "medium"
CHECKED = [
    "overflow_episodes", "uncontained_waste", "total_distance", "avg_waiting_completed",
    "unserved_requests", "avg_bins_per_trip", "requests_raised", "completed_pickups",
    "max_waiting_completed", "max_open_wait", "full_bins_at_end", "truck_utilisation",
    "mean_queue_length", "units_collected", "waste_offered", "net_bin_fill_change",
]


def dashboard_models(seed, level):
    """Exactly what app.build_models does, without starting Streamlit."""
    from model import BaselineModel, SmartModel

    scenario = generate_scenario(seed)
    city_arrival = city_arrival_rate(generate_scenarios(C.N_SCENARIOS, C.BASE_SEED))
    load_scale = C.load_scale_for(C.level_share(level), city_arrival)
    models = {
        "baseline": BaselineModel(scenario, load_scale=load_scale),
        "smart": SmartModel(scenario, load_scale=load_scale),
    }
    return scenario, models


def main():
    saved = pd.read_csv("results.csv")
    scenario, models = dashboard_models(SEED, LEVEL)

    print(f"dashboard: seed {SEED}, load {LEVEL!r}, {models['smart'].horizon} steps")
    print("stepping both models to the end, one step at a time...")

    steps = 0
    while not models["smart"].is_done:
        for model in models.values():
            model.step()
        steps += 1
    print(f"stepped {steps} times\n")

    # the scenario must be exactly what the generator produces
    assert scenario == generate_scenario(SEED), "the scenario was changed"

    failures = 0
    for strategy in ("baseline", "smart"):
        summary = models[strategy].metrics.summary()
        row = saved[(saved.scenario == SEED) & (saved.load == LEVEL)
                    & (saved.strategy == strategy)].iloc[0]
        print(f"  {strategy}:")
        for column in CHECKED:
            mine, theirs = summary[column], row[column]
            if pd.isna(mine) and pd.isna(theirs):
                print(f"    {column:<24} both n/a")
                continue
            same = abs(float(mine) - float(theirs)) < 1e-6
            failures += not same
            print(f"    {column:<24} dashboard {str(mine):>12}   results.csv {str(theirs):>12}"
                  f"   {'ok' if same else 'MISMATCH'}")

    print()
    assert failures == 0, f"{failures} metrics do not match results.csv"
    print("PASS: the dashboard reproduces results.csv exactly for this seed")


if __name__ == "__main__":
    main()