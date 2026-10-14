"""Pre-generated, reproducible scenarios.

A scenario fixes *everything that can happen during a run*:

* where the bins are,
* how full they start,
* how much waste drops into each bin at each step,
* how warm each bin is at each step.

The scenario is built once from a seed and then only read. Because of that
both strategies see exactly the same city, and no strategy ever calls the
random generator while it runs - the only difference between them is how
they decide where to drive.

The amount of waste is the *nominal* amount. How busy the city actually is
decided by the load level, which the model applies as a multiplier while it
reads the arrivals (see `config.load_scale_for`).
"""

from dataclasses import dataclass
import random

from config import (
    ARRIVAL_NOISE,
    COOLING_FACTOR,
    GRID_HEIGHT,
    GRID_WIDTH,
    HORIZON,
    INCIDENT_CHANCE,
    MAX_ARRIVAL_RATE,
    MAX_INCIDENT_STEPS,
    MIN_ARRIVAL_RATE,
    MIN_INCIDENT_STEPS,
    N_BINS,
    PEAK_INCIDENT_TEMP,
    RISE_STEPS,
    AMBIENT_TEMP,
    DEPOT_POS,
)


@dataclass
class Scenario:
    """A complete, replayable day in the life of the city."""

    seed: int
    horizon: int
    bin_positions: list  # [(x, y), ...]
    initial_levels: list  # units already in the bin at step 0
    arrivals: list  # arrivals[step][bin] -> units of waste added at that step
    temperatures: list  # temperatures[step][bin] -> degrees C at that step


def _sample_positions(rng, n_bins, width, height, depot):
    """Pick `n_bins` different cells for the bins, never on the depot."""
    cells = [(x, y) for x in range(width) for y in range(height) if (x, y) != depot]
    if n_bins > len(cells):  # small grids in the demo scripts
        raise ValueError("not enough free cells for that many bins")
    return rng.sample(cells, n_bins)


def _make_arrivals(rng, n_bins, horizon, arrival_range):
    """Waste that drops into every bin at every step.

    Each bin gets its own average arrival rate (some streets are busier
    than others). The amount that drops in on one particular step jitters
    around that rate.
    """
    low, high = arrival_range
    rates = [rng.uniform(low, high) for _ in range(n_bins)]
    arrivals = []
    for _ in range(horizon):
        arrivals.append([max(0.0, round(rng.gauss(rate, ARRIVAL_NOISE), 2)) for rate in rates])
    return arrivals


def _bin_temperature(rng, horizon, incident_chance):
    """Ambient temperature, with at most one fire incident.

    While the fire burns the bin heats up towards its peak, afterwards it
    cools down geometrically back to the ambient temperature.
    """
    if rng.random() > incident_chance:
        return [AMBIENT_TEMP] * horizon

    start = rng.randrange(horizon)
    duration = rng.randint(MIN_INCIDENT_STEPS, MAX_INCIDENT_STEPS)
    series = []
    value = AMBIENT_TEMP
    for step in range(horizon):
        if start <= step < start + duration:
            elapsed = step - start + 1
            value = min(PEAK_INCIDENT_TEMP, PEAK_INCIDENT_TEMP * elapsed / RISE_STEPS)
        elif step >= start + duration and value > AMBIENT_TEMP:
            value = AMBIENT_TEMP + (value - AMBIENT_TEMP) * COOLING_FACTOR
        series.append(round(value, 1))
    return series


def _make_temperatures(rng, n_bins, horizon, incident_chance):
    """One temperature series per bin, written as temperatures[step][bin]."""
    per_bin = [_bin_temperature(rng, horizon, incident_chance) for _ in range(n_bins)]
    return [list(row) for row in zip(*per_bin)]


def generate_scenario(
    seed,
    n_bins=N_BINS,
    horizon=HORIZON,
    width=GRID_WIDTH,
    height=GRID_HEIGHT,
    depot=DEPOT_POS,
    incident_chance=INCIDENT_CHANCE,
    arrival_range=(MIN_ARRIVAL_RATE, MAX_ARRIVAL_RATE),
):
    """Build one scenario from a seed. Same seed -> exactly the same day."""
    rng = random.Random(seed)
    return Scenario(
        seed=seed,
        horizon=horizon,
        bin_positions=_sample_positions(rng, n_bins, width, height, depot),
        initial_levels=[round(rng.uniform(10.0, 80.0), 1) for _ in range(n_bins)],
        arrivals=_make_arrivals(rng, n_bins, horizon, arrival_range),
        temperatures=_make_temperatures(rng, n_bins, horizon, incident_chance),
    )


def generate_scenarios(n, base_seed):
    """The `n` scenarios of the experiment, with seeds base_seed ... +n-1."""
    return [generate_scenario(base_seed + i) for i in range(n)]


def city_arrival_rate(scenarios):
    """Waste per step these scenarios produce before the load scale.

    Measured rather than assumed: every arrival is clipped at zero, which
    pushes the average a little above the nominal rate of the generator, and
    the load levels have to be built on the real number.
    """
    total = sum(sum(sum(step) for step in sc.arrivals) for sc in scenarios)
    steps = len(scenarios) * scenarios[0].horizon
    return total / steps