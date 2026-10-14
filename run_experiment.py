"""Compare the collection strategies over many scenarios and load levels.

    python run_experiment.py

The script works in four steps:

1. **calibration** - run the *baseline only* over the four load levels and
   pick the highest level where the baseline still keeps up. The rule is
   fixed before any smart run happens, and no parameter is tuned to make
   one strategy look better than the other.
2. **experiment** - run every strategy on the same scenarios at every load
   level, including a "no collection" run as the lower bound.
3. **tables and charts** - print the comparison, write `results.csv` and one
   bar chart per metric.
4. **tradeoffs** - describe what the numbers actually show, including the
   cases where the baseline beats the dispatcher.
"""

import matplotlib

matplotlib.use("Agg")  # no window, we only save pictures
import matplotlib.pyplot as plt
import pandas as pd

import config as C
from model import BaselineModel, NoCollectionModel, SmartModel
from scenario import city_arrival_rate, generate_scenario, generate_scenarios

STRATEGIES = {
    "baseline": BaselineModel,
    "smart": SmartModel,
    "no collection": NoCollectionModel,
}

# the six metrics the comparison is about
METRICS = [
    ("overflow_episodes", "overflow episodes"),
    ("uncontained_waste", "uncontained waste"),
    ("total_distance", "total distance"),
    ("avg_waiting_completed", "avg wait (served)"),
    ("unserved_requests", "unserved requests"),
    ("avg_bins_per_trip", "bins per trip"),
]

# added in stage 4b, reported in the same table below the six above
EXTRA_METRICS = [
    ("max_waiting_completed", "max wait (served)"),
    ("max_open_wait", "max wait (still open)"),
    ("full_bins_at_end", "bins at 100 % at end"),
    ("truck_utilisation", "truck utilisation"),
    ("mean_queue_length", "mean queue length"),
    ("units_collected", "units collected"),
    ("mean_units_per_step", "units collected/step"),
    ("best_sustained_rate", "best sustained/step"),
    ("peak_units_in_step", "busiest single step"),
]


def level_label(level):
    """Chart tick label.

    The levels are fractions of the never-idle capacity estimate of 4c, and
    the 130 % level is spelled out that way instead of as plain "130 %".
    """
    share = C.level_share(level)
    mark = "\n(added later)" if level in C.EXTENSION_LOAD_LEVELS else ""
    if level == "overload":
        return f"{level}\n{share:.0%} of the never-idle\ncapacity estimate"
    return f"{level}\n{share:.0%}{mark}"


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------

# Bin levels are stored rounded to two decimals, so the mass balance can be
# off by a hundredth of a unit per bin. Anything bigger than that is a bug.
MASS_BALANCE_TOLERANCE = 0.25


def check_run(strategy, summary, balance):
    """The two invariants every single run has to satisfy."""
    assert (
        summary["requests_raised"]
        == summary["completed_pickups"] + summary["unserved_requests"]
    ), (
        f"{strategy}: {summary['requests_raised']} requests raised, "
        f"{summary['completed_pickups']} served, {summary['unserved_requests']} still open"
    )
    assert abs(balance["residual"]) < MASS_BALANCE_TOLERANCE, (
        f"{strategy}: waste does not add up, offered {balance['offered']:.2f} but "
        f"collected {balance['collected']:.2f} + uncontained "
        f"{balance['uncontained']:.2f} + change {balance['net_change']:.2f} "
        f"leaves {balance['residual']:+.4f}"
    )


def run_one(strategy, scenario, level_name, city_arrival):
    """Run one strategy on one scenario at one load level."""
    load_scale = C.load_scale_for(C.level_share(level_name), city_arrival)
    model = STRATEGIES[strategy](scenario, load_scale=load_scale)
    summary = model.run()
    balance = model.metrics.mass_balance()
    check_run(f"{strategy} on seed {scenario.seed}", summary, balance)
    return {
        "scenario": scenario.seed,
        "load": level_name,
        "load_scale": round(load_scale, 4),
        "strategy": strategy,
        **summary,
        "mass_balance_residual": round(balance["residual"], 4),
    }


def run_level(scenarios, level_name, city_arrival):
    """Every strategy, every scenario, one load level."""
    rows = [run_one(strategy, sc, level_name, city_arrival)
            for sc in scenarios for strategy in STRATEGIES]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# step 1: calibration, baseline only
# --------------------------------------------------------------------------


def calibrate_reference_load(scenarios, city_arrival):
    """The highest load level where the baseline still keeps up.

    Decision rule, fixed before the smart strategy is ever run:
    a run is *saturated* when more than `SATURATION_SHARE` of the bins are
    still waiting for a collection at the end of the day. The reference load
    is the highest load level whose *average* over all scenarios stays at or
    below that share.
    """
    print("=" * 78)
    print("STEP 1 - calibration with the BASELINE only (the smart strategy has not run yet)")
    print("=" * 78)
    print(f"A run counts as saturated when more than {C.SATURATION_SHARE:.0%} of the "
          f"{C.N_BINS} bins are still unserved at the end.\n")
    print(f"{'level':<10}{'load scale':>12}{'waste/step':>13}{'mean unserved':>16}"
          f"{'share':>9}  saturated")

    shares = {}
    for level_name, level in C.LOAD_LEVELS.items():
        frame = pd.DataFrame([run_one("baseline", sc, level_name, city_arrival)
                              for sc in scenarios])
        share = frame["unserved_requests"].mean() / C.N_BINS
        shares[level_name] = share
        print(f"{level_name:<10}{C.load_scale_for(level, city_arrival):>12.3f}"
              f"{level * C.FLEET_THROUGHPUT:>13.2f}"
              f"{frame['unserved_requests'].mean():>16.1f}{share:>9.1%}"
              f"  {'YES' if share > C.SATURATION_SHARE else 'no'}")

    # The highest level that still keeps up (levels are checked richest first).
    reference = next(
        name for name in reversed(list(C.LOAD_LEVELS)) if shares[name] <= C.SATURATION_SHARE
    )
    print(f"\nReference load = {reference!r} "
          f"({C.level_share(reference):.0%} of the fleet's throughput, "
          f"{shares[reference]:.1%} of the bins left waiting on average).")
    print("This rule only looked at the baseline. No parameter is changed anywhere.\n")
    return reference


# --------------------------------------------------------------------------
# step 3: tables and charts
# --------------------------------------------------------------------------


def mean_and_std(series):
    """`12.34 ± 4.56`, or a note when there is too little to average."""
    values = series.dropna()
    if values.empty:
        return "n/a"
    if len(values) == 1:
        return f"{values.mean():,.2f} (1 run)"
    return f"{values.mean():,.2f} ± {values.std():,.2f}"


def print_table(frame, title):
    """One row per metric, one column per strategy."""
    print("=" * 78)
    print(title)
    print("=" * 78)
    header = f"{'metric':<22}" + "".join(f"{s:>22}" for s in STRATEGIES)
    print(header)
    print("-" * len(header))
    for column, label in METRICS + EXTRA_METRICS:
        cells = [
            mean_and_std(frame[frame["strategy"] == s][column]) for s in STRATEGIES
        ]
        print(f"{label:<22}" + "".join(f"{cell:>22}" for cell in cells))


COLORS = {"baseline": "tab:orange", "smart": "tab:blue", "no collection": "tab:gray"}


def save_charts(frame):
    """One bar chart per metric, grouped by load level, coloured by strategy."""
    levels = list(C.ALL_LOAD_LEVELS)
    positions = range(len(levels))
    width = 0.8 / len(STRATEGIES)

    for column, label in METRICS + EXTRA_METRICS:
        figure, axes = plt.subplots(figsize=(9, 4.5))
        for index, strategy in enumerate(STRATEGIES):
            means, stds = [], []
            for level in levels:
                values = frame[(frame["load"] == level)
                               & (frame["strategy"] == strategy)][column].dropna()
                means.append(values.mean() if not values.empty else 0)
                stds.append(values.std() if len(values) > 1 else 0)
            xs = [p + (index - 1) * width for p in positions]
            axes.bar(xs, means, width=width, yerr=stds, capsize=3,
                     label=strategy, color=COLORS[strategy])

        axes.set_xticks(list(positions))
        axes.set_xticklabels([level_label(level) for level in levels], fontsize=8)
        axes.set_xlabel("load level")
        axes.set_ylabel(label)
        axes.set_title(f"{label} by load level (mean ± standard deviation)")
        axes.legend()
        axes.grid(axis="y", alpha=0.3)
        figure.tight_layout(rect=(0, 0.06, 1, 1))
        utilisation = level_means(frame, "overload", "smart")["truck_utilisation"].mean()
        figure.text(
            0.01, 0.015,
            f"All levels are fractions of the never-idle capacity estimate "
            f"({C.FLEET_THROUGHPUT:.2f} units/step). Measured smart truck utilisation at 130 %: "
            f"{utilisation:.2f}.",
            fontsize=7, color="0.35",
        )
        figure.savefig(f"chart_{column}.png", dpi=120)
        plt.close(figure)
        print(f"  wrote chart_{column}.png")


# --------------------------------------------------------------------------
# step 4: what the numbers actually say
# --------------------------------------------------------------------------

# For five of the six metrics a lower number is better. "bins per trip" is the
# exception: covering more bins with one trip means the trip was used well.
DIRECTION = {
    "overflow_episodes": "lower",
    "uncontained_waste": "lower",
    "total_distance": "lower",
    "avg_waiting_completed": "lower",
    "unserved_requests": "lower",
    "avg_bins_per_trip": "higher",
}


def better(column, baseline_value, smart_value):
    """Which of the two strategies has the better value for this metric."""
    if pd.isna(baseline_value) or pd.isna(smart_value):
        return "n/a"
    if baseline_value == smart_value:
        return "tie"
    if DIRECTION[column] == "lower":
        return "baseline" if baseline_value < smart_value else "smart"
    return "baseline" if baseline_value > smart_value else "smart"


def level_means(frame, level, strategy):
    return frame[(frame["load"] == level) & (frame["strategy"] == strategy)]


def print_mass_balance(frame):
    """ADDED AFTER SEEING THE 4b RESULTS.

    Where the offered waste ended up, per load level and strategy. If the
    book-keeping does not close, the model is losing or inventing waste.
    """
    print("=" * 78)
    print("MASS BALANCE (added after seeing the 4b results)")
    print("=" * 78)
    print("offered = collected + uncontained + (bin contents at the end - at the start)\n")
    print(f"{'load':<10}{'strategy':<14}{'offered':>11}{'collected':>11}"
          f"{'uncontained':>13}{'net change':>12}{'worst gap':>12}")
    for level in C.ALL_LOAD_LEVELS:
        for strategy in ("baseline", "smart", "no collection"):
            runs = level_means(frame, level, strategy)
            collected = runs["units_collected"].mean()
            uncontained = runs["uncontained_waste"].mean()
            net = runs["net_bin_fill_change"].mean()
            offered = collected + uncontained + net
            worst = runs["mass_balance_residual"].abs().max()
            print(f"{level:<10}{strategy:<14}{offered:>11,.1f}{collected:>11,.1f}"
                  f"{uncontained:>13,.1f}{net:>+12,.1f}{worst:>12.4f}")
        print()
    print(f"Every run asserts the identity itself; the largest gap seen anywhere is "
          f"{frame['mass_balance_residual'].abs().max():.4f} units, which is the rounding of "
          f"the bin levels and not a leak.\n")


# --------------------------------------------------------------------------
# duration sweep, added after seeing the 4b results
# --------------------------------------------------------------------------

DURATION_FACTORS = (1, 3, 6)
DURATION_METRICS = [
    ("overflow_episodes", "overflow"),
    ("uncontained_waste", "uncontained"),
    ("unserved_requests", "unserved"),
    ("mean_queue_length", "queue"),
    ("net_bin_fill_change", "net fill"),
]


def run_duration_sweep(scenarios, city_arrival):
    """The four original load levels at 1x, 3x and 6x the length of a day.

    A longer day is a longer generated day, not the same day run twice: the
    same seed gives the same city and the same first steps, only the waste
    keeps coming for longer.
    """
    rows = []
    for level in C.LOAD_LEVELS:
        load_scale = C.load_scale_for(C.level_share(level), city_arrival)
        for factor in DURATION_FACTORS:
            horizon = C.HORIZON * factor
            longer_days = [generate_scenario(sc.seed, horizon=horizon) for sc in scenarios]
            for original, longer in zip(scenarios, longer_days):
                assert original.bin_positions == longer.bin_positions
                assert original.initial_levels == longer.initial_levels
            for strategy in ("baseline", "smart"):
                for day in longer_days:
                    model = STRATEGIES[strategy](day, load_scale=load_scale)
                    summary = model.run()
                    balance = model.metrics.mass_balance()
                    check_run(f"{strategy} seed {day.seed} {factor}x", summary, balance)
                    rows.append({
                        "load": level,
                        "duration": f"{factor}x",
                        "steps": horizon,
                        "strategy": strategy,
                        "scenario": day.seed,
                        **summary,
                    })
    return pd.DataFrame(rows)


def print_duration_sweep(frame):
    """ADDED AFTER SEEING THE 4b RESULTS."""
    print("=" * 78)
    print("DURATION SWEEP (added after seeing the 4b results)")
    print("=" * 78)
    print(f"The four original load levels, at 1x ({C.HORIZON} steps), 3x and 6x the length of "
          f"a day, {len(frame) // (len(C.LOAD_LEVELS) * len(DURATION_FACTORS) * 2)} scenarios "
          f"per cell, both strategies.\n")

    for level in C.LOAD_LEVELS:
        print(f"load {level!r} ({C.level_share(level):.0%} of the never-idle capacity estimate):")
        header = f"   {'length':<17}{'strategy':<11}" + "".join(f"{label:>14}" for _, label in DURATION_METRICS)
        print(header)
        print("   " + "-" * (len(header) - 3))
        for factor in DURATION_FACTORS:
            for strategy in ("baseline", "smart"):
                cell = frame[(frame["load"] == level)
                             & (frame["duration"] == f"{factor}x")
                             & (frame["strategy"] == strategy)]
                cells = [mean_and_std(cell[column]) for column, _ in DURATION_METRICS]
                print(f"   {f'{factor}x ({C.HORIZON * factor} steps)':<17}"
                      f"{strategy:<11}" + "".join(f"{value:>14}" for value in cells))
        print()

    # question 3: does smart keep its zero overflow on a longer day?
    print("Does smart keep zero overflow at 130 % when the day gets longer?")
    reference = "overload"
    for factor in DURATION_FACTORS:
        cell = frame[(frame["load"] == reference)
                     & (frame["duration"] == f"{factor}x")
                     & (frame["strategy"] == "smart")]
        spilling = int((cell["overflow_episodes"] > 0).sum())
        print(f"   {factor}x ({C.HORIZON * factor:4d} steps): "
              f"{cell['overflow_episodes'].mean():6.2f} overflow episodes on average, "
              f"{cell['uncontained_waste'].mean():8.2f} units uncontained, "
              f"{spilling:2d} of {len(cell)} scenarios spill at all")
    first = next((factor for factor in DURATION_FACTORS
                  if (frame[(frame["load"] == reference)
                            & (frame["duration"] == f"{factor}x")
                            & (frame["strategy"] == "smart")]["overflow_episodes"] > 0).any()),
                 None)
    if first is None:
        print("   -> smart never overflows at this load, at any length that was run")
    else:
        print(f"   -> smart first spills at {first}x, that is after "
              f"{C.HORIZON * first} steps ({first} days)")
    print()


def print_throughput_check(frame, reference):
    """Does the README formula match what the strategies really collect?

    The formula assumes a fleet that never idles and empties exactly one bin
    per round trip, so it can only be compared with a *sustained* rate: the
    busiest single step is not a fair yardstick, because when all trucks
    happen to finish loading on the same step it is several times higher.
    """
    print("=" * 78)
    print("THROUGHPUT FORMULA vs WHAT THE STRATEGIES REALLY ACHIEVE")
    print("=" * 78)
    print(f"The README formula says the fleet can collect {C.FLEET_THROUGHPUT:.2f} units per "
          f"step:\n   {C.N_COLLECTORS} trucks * {C.TRUCK_CAPACITY:.0f} units / "
          f"{C.ROUND_TRIP_STEPS:.0f} step round trip = {C.FLEET_THROUGHPUT:.2f} units/step")

    print(f"\n{'load':<10}{'strategy':<14}{'sustained/step':>16}{'day average/step':>19}"
          f"{'busiest step':>15}")
    for level in C.LOAD_LEVELS:
        for strategy in ("baseline", "smart"):
            runs = level_means(frame, level, strategy)
            print(f"{level:<10}{strategy:<14}"
                  f"{runs['best_sustained_rate'].mean():>16.2f}"
                  f"{runs['mean_units_per_step'].mean():>19.2f}"
                  f"{runs['peak_units_in_step'].mean():>15.2f}")

    smart = level_means(frame, reference, "smart")
    baseline = level_means(frame, reference, "baseline")
    formula = C.FLEET_THROUGHPUT
    sustained = smart["best_sustained_rate"].mean()
    day_average = smart["mean_units_per_step"].mean()
    busy_step = smart["peak_units_in_step"].mean()

    print(f"\n  At the reference load {reference!r} the formula predicts "
          f"{formula:.2f} units per step.")
    baseline_rate = baseline["mean_units_per_step"].mean()
    print(f"\n  The baseline collects {baseline_rate:.2f} units/step over the whole day, "
          f"{baseline_rate / formula:.0%} of the formula - a difference of "
          f"{abs(baseline_rate - formula):.2f} units/step. It never idles "
          f"(utilisation {baseline['truck_utilisation'].mean():.0%}), which is exactly the "
          f"assumption the formula makes, so this is the case where it holds.")

    print(f"\n  The dispatcher collects {day_average:.2f} units/step over the whole day, only "
          f"{day_average / formula:.0%} of the formula. Here they disagree, and the formula "
          f"is too high by {formula - day_average:.2f} units/step "
          f"({1 - day_average / formula:.0%}).")
    print(f"  The reason is visible in the utilisation: {smart['truck_utilisation'].mean():.0%} "
          f"against {baseline['truck_utilisation'].mean():.0%}. The dispatcher stands still "
          f"until a bin crosses {C.COLLECTION_THRESHOLD:.0f} units, and at this load it is "
          f"waiting most of the time, so it never approaches its own capacity.")

    print(f"\n  Two numbers that must not be read as 'the fleet can collect this much':")
    print(f"    - {busy_step:.2f} units in the busiest single step "
          f"({busy_step / formula:.0%} of the formula). That is a coincidence of all trucks "
          f"finishing a load on the same step; it lasts one step.")
    print(f"    - {sustained:.2f} units/step over the best stretch of "
          f"{C.ROUND_TRIP_STEPS:.0f} steps ({sustained / formula:.0%} of the formula). This one "
          f"is *above* the formula, because the formula uses the average bin distance of "
          f"{C.TYPICAL_BIN_DISTANCE:.1f} cells while a burst can happen on the bins closest to "
          f"the depot.")
    print(f"\n  Conclusion: the formula is a capacity estimate for a fleet working flat out. It "
          f"describes the baseline within {abs(baseline_rate - formula) / formula:.0%} and "
          f"overstates the dispatcher by {1 - day_average / formula:.0%}. It should not be used "
          f"as a prediction of what the smart strategy will collect.")


def print_breaking_points(frame):
    """Where does each strategy stop coping, and where does smart first spill?"""
    print("=" * 78)
    print("WHERE EACH STRATEGY BREAKS (across all load levels)")
    print("=" * 78)
    print(f"A strategy is said to be over its limit at a level when more than "
          f"{C.SATURATION_SHARE:.0%} of the bins are still unserved at the end.\n")

    for strategy in ("baseline", "smart", "no collection"):
        first = None
        for level in C.ALL_LOAD_LEVELS:
            share = level_means(frame, level, strategy)["unserved_requests"].mean() / C.N_BINS
            if share > C.SATURATION_SHARE:
                first = level
                break
        if first is None:
            print(f"  {strategy:<14} never passes 20 % unserved, not even at "
                  f"{C.EXTENSION_LOAD_LEVELS['ext_500']:.0%}")
        else:
            print(f"  {strategy:<14} first passes 20 % unserved at {first!r} "
                  f"({C.level_share(first):.0%} of the fleet's throughput)")

    print("\n  first level where the smart strategy spills at all "
          "(at least one scenario with an overflow episode):")
    first_overflow = None
    for level in C.ALL_LOAD_LEVELS:
        runs = level_means(frame, level, "smart")
        if (runs["overflow_episodes"] > 0).any():
            first_overflow = level
            print(f"    {level!r} ({C.level_share(level):.0%}): mean "
                  f"{runs['overflow_episodes'].mean():.2f} overflow episodes, "
                  f"{runs['uncontained_waste'].mean():.2f} units uncontained")
            break
    if first_overflow is None:
        print("    never, at any level that was run")


def print_tradeoffs(frame, reference, scenarios):
    """Describe the result, including where the baseline is the better one."""
    levels = list(C.LOAD_LEVELS)
    print("=" * 78)
    print("STEP 4 - what the numbers actually say")
    print("=" * 78)

    # 1. head to head at the reference load, next to the lower bound
    print(f"\n1. At the reference load {reference!r} "
          f"({C.level_share(reference):.0%} of the fleet's throughput):")
    print(f"   {'metric':<22}{'baseline':>13}{'smart':>13}{'none':>13}   better")
    for column, label in METRICS:
        b = level_means(frame, reference, "baseline")[column].mean()
        s = level_means(frame, reference, "smart")[column].mean()
        n = level_means(frame, reference, "no collection")[column].mean()
        none = "n/a" if pd.isna(n) else f"{n:,.2f}"
        print(f"   {label:<22}{b:>13,.2f}{s:>13,.2f}{none:>13}   {better(column, b, s)}")

    # 2. the winner per metric at every load level
    print("\n2. Who is better at each load level:")
    print(f"   {'metric':<22}" + "".join(f"{level:>12}" for level in levels))
    for column, label in METRICS:
        cells = []
        for level in levels:
            b = level_means(frame, level, "baseline")[column].mean()
            s = level_means(frame, level, "smart")[column].mean()
            cells.append(better(column, b, s))
        print(f"   {label:<22}" + "".join(f"{cell:>12}" for cell in cells))

    # 3. every case where the baseline comes out better
    print("\n3. Cases where the BASELINE beats the dispatcher:")
    baseline_wins = []
    for level in levels:
        for column, label in METRICS:
            b = level_means(frame, level, "baseline")[column].mean()
            s = level_means(frame, level, "smart")[column].mean()
            if better(column, b, s) == "baseline":
                line = f"{label} at load {level!r} (baseline {b:,.2f} vs smart {s:,.2f})"
                if column == "avg_waiting_completed":
                    # the average is only as reliable as the number of
                    # requests that were actually served behind it
                    served = level_means(frame, level, "baseline")["completed_pickups"].mean()
                    if served < 5:
                        line += f" - but only {served:.1f} served requests per run"
                baseline_wins.append(line)
    for line in baseline_wins or ["none at any load level"]:
        print(f"   - {line}")

    # 4. how often, scenario by scenario, at the reference load
    merged = level_means(frame, reference, "baseline").merge(
        level_means(frame, reference, "smart"), on="scenario", suffixes=("_b", "_s")
    )
    print(f"\n4. At the reference load, in how many of the {len(scenarios)} single scenarios "
          f"the baseline had the better value:")
    for column, label in METRICS:
        wins = sum(1 for _, row in merged.iterrows()
                   if better(column, row[f"{column}_b"], row[f"{column}_s"]) == "baseline")
        if wins:
            print(f"   - {label}: baseline better in {wins} of {len(scenarios)} scenarios")

    # 5. does the dispatcher starve the far bins?
    print("\n5. Does the dispatcher starve the bins far from the depot?")
    print(f"   a bin of this city sits {C.TYPICAL_BIN_DISTANCE:.1f} cells from the depot "
          f"on average. Bins still waiting at the end of the day:")
    for level in levels:
        b = level_means(frame, level, "baseline")["unserved_mean_distance"].mean()
        s = level_means(frame, level, "smart")["unserved_mean_distance"].mean()
        print(f"   - load {level:<9} baseline {b:5.1f} cells, smart {s:5.1f} cells")

    # 6. the two structural reasons that show up everywhere
    print("\n6. Two things that explain most of the difference:")
    distance = {level: level_means(frame, level, "baseline")["total_distance"].mean()
                for level in levels}
    print(f"   - The baseline drives its full route whatever happens: "
          f"{min(distance.values()):,.0f} to {max(distance.values()):,.0f} cells at every load "
          f"level. The dispatcher only drives when a bin is above "
          f"{C.COLLECTION_THRESHOLD:.0f} units, so its distance grows with the waste: "
          + ", ".join(f"{level} {level_means(frame, level, 'smart')['total_distance'].mean():,.0f}"
                      for level in levels) + " cells.")
    at_ref = level_means(frame, reference, "baseline")
    smart_ref = level_means(frame, reference, "smart")
    print(f"   - At the reference load the baseline makes {at_ref['pickups'].mean():,.0f} "
          f"pickups but only {at_ref['completed_pickups'].mean():,.0f} of them answer a "
          f"request; the rest are taken from bins nobody asked about. The dispatcher's "
          f"{smart_ref['pickups'].mean():,.0f} pickups all answer a request.")


def assert_sweep_matches_main(main_frame, sweep_frame):
    """The 1x cells of the sweep must reproduce the main experiment exactly."""
    for level in C.LOAD_LEVELS:
        for strategy in ("baseline", "smart"):
            main_mean = level_means(main_frame, level, strategy)["overflow_episodes"].mean()
            sweep_mean = sweep_frame[(sweep_frame["load"] == level)
                                     & (sweep_frame["duration"] == "1x")
                                     & (sweep_frame["strategy"] == strategy)
                                     ]["overflow_episodes"].mean()
            assert abs(main_mean - sweep_mean) < 1e-9, (
                f"{level}/{strategy}: the sweep gives {sweep_mean}, the main experiment "
                f"{main_mean}"
            )


def main():
    scenarios = generate_scenarios(C.N_SCENARIOS, C.BASE_SEED)
    print(f"{len(scenarios)} scenarios (seeds {C.BASE_SEED} .. {C.BASE_SEED + len(scenarios) - 1}),"
          f" {C.N_BINS} bins, {C.N_COLLECTORS} trucks, {C.HORIZON} steps per day")
    city_arrival = city_arrival_rate(scenarios)
    print(f"these scenarios produce {city_arrival:.2f} units of waste per step before "
          f"scaling\n")

    reference = calibrate_reference_load(scenarios, city_arrival)

    print("=" * 78)
    print(f"STEP 2 - experiment: {len(scenarios)} scenarios x "
          f"{len(C.ALL_LOAD_LEVELS)} load levels x {len(STRATEGIES)} strategies")
    print("=" * 78)
    frames = [run_level(scenarios, level, city_arrival) for level in C.ALL_LOAD_LEVELS]
    frame = pd.concat(frames, ignore_index=True)
    frame.to_csv("results.csv", index=False)
    print(f"  wrote results.csv ({len(frame)} runs)\n")

    for level in C.ALL_LOAD_LEVELS:
        share = C.level_share(level)
        added = "  [added after seeing the first sweep]" if level in C.EXTENSION_LOAD_LEVELS else ""
        print_table(frame[frame["load"] == level],
                    f"Load level {level!r} - {share:.0%} of fleet throughput, "
                    f"load scale {C.load_scale_for(share, city_arrival):.3f}{added}")

    print("\n" + "=" * 78)
    print("STEP 3 - charts")
    print("=" * 78)
    save_charts(frame)

    print()
    print_throughput_check(frame, reference)

    print()
    print_breaking_points(frame)

    print()
    print_tradeoffs(frame, reference, scenarios)

    print()
    print_mass_balance(frame)

    print()
    duration_frame = run_duration_sweep(scenarios, city_arrival)
    duration_frame.to_csv("results_duration.csv", index=False)
    assert_sweep_matches_main(frame, duration_frame)
    print(f"  wrote results_duration.csv ({len(duration_frame)} runs), and its 1x cells match "
          f"the main experiment exactly\n")
    print_duration_sweep(duration_frame)


if __name__ == "__main__":
    main()