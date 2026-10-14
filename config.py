"""Every tunable number of the system lives here.

Keeping the parameters in one file makes the model easy to explain and easy
to experiment with: change a value here and both strategies change with it.
"""

import math

# --------------------------------------------------------------------------
# City layout
# --------------------------------------------------------------------------
GRID_WIDTH = 20
GRID_HEIGHT = 20
N_BINS = 30
N_COLLECTORS = 3
DEPOT_POS = (10, 10)  # the depot sits in the middle of the city

# --------------------------------------------------------------------------
# Bins
# --------------------------------------------------------------------------
BIN_CAPACITY = 100.0  # units of waste a bin can hold
NEARLY_FULL_LEVEL = 70.0  # status becomes "nearly_full"
COLLECTION_THRESHOLD = 90.0  # a bin at this level asks for a collection

# --------------------------------------------------------------------------
# Safety (temperature alerts)
# --------------------------------------------------------------------------
TEMP_THRESHOLD = 45.0  # degrees C that switches the safety alert on
ALERT_DURATION = 5  # steps an alert stays active before it clears
AMBIENT_TEMP = 20.0  # C in a healthy, closed bin

# --------------------------------------------------------------------------
# Collector trucks
# --------------------------------------------------------------------------
TRUCK_CAPACITY = 20.0  # units one truck carries per trip
TRUCK_SPEED = 2  # cells covered per model step (one step = half an hour)
# Loading a bin and unloading at the depot each take one step.

# --------------------------------------------------------------------------
# Generated demand (used by scenario.py)
# --------------------------------------------------------------------------
MIN_ARRIVAL_RATE = 0.15  # units/step of the quietest bin
MAX_ARRIVAL_RATE = 0.45  # units/step of the busiest bin
ARRIVAL_NOISE = 0.8  # how much a single step deviates from that rate
INCIDENT_CHANCE = 0.25  # share of bins that catch fire at some point
PEAK_INCIDENT_TEMP = 85.0  # C at the hottest moment of a fire
RISE_STEPS = 2  # steps a burning bin needs to reach its peak
COOLING_FACTOR = 0.6  # how fast a bin cools down after the fire is out
MIN_INCIDENT_STEPS = 3
MAX_INCIDENT_STEPS = 7

# --------------------------------------------------------------------------
# Load calibration
# --------------------------------------------------------------------------


def _mean_grid_distance(width, height, depot):
    """Average distance from the depot to a bin placed anywhere on the grid.

    A bin can sit on any cell, so the average is the average horizontal gap
    to the depot plus the average vertical gap.
    """
    horizontal = sum(abs(x - depot[0]) for x in range(width)) / width
    vertical = sum(abs(y - depot[1]) for y in range(height)) / height
    return horizontal + vertical


# How far a truck has to drive to reach a typical bin, and back again.
# The two extra steps are loading the bin and unloading at the depot.
TYPICAL_BIN_DISTANCE = _mean_grid_distance(GRID_WIDTH, GRID_HEIGHT, DEPOT_POS)
ROUND_TRIP_STEPS = 2 * TYPICAL_BIN_DISTANCE / TRUCK_SPEED + 2
FLEET_THROUGHPUT = N_COLLECTORS * TRUCK_CAPACITY / ROUND_TRIP_STEPS  # units per step

# The generated day is *designed* to produce this much waste per step, but
# the real number is measured from the scenarios themselves, because the
# arrivals are clipped at zero, which pushes the average up.
NOMINAL_CITY_ARRIVAL = N_BINS * (MIN_ARRIVAL_RATE + MAX_ARRIVAL_RATE) / 2

# Load levels, as a fraction of what the fleet can collect.
LOAD_LEVELS = {"low": 0.4, "medium": 0.7, "high": 1.0, "overload": 1.3}

# ADDED AFTER SEEING THE FIRST SWEEP. The four levels above never made the
# baseline saturate, so three heavier levels were added afterwards to find
# out where each strategy actually breaks. They are only used for that:
# the calibration rule and the reference load are decided by the original
# four levels alone and have not been changed since.
EXTENSION_LOAD_LEVELS = {"ext_200": 2.0, "ext_300": 3.0, "ext_500": 5.0}

# everything the experiment runs, original levels first
ALL_LOAD_LEVELS = {**LOAD_LEVELS, **EXTENSION_LOAD_LEVELS}

# A run counts as saturated when more than this share of bins is still
# waiting for a collection at the end of the day.
SATURATION_SHARE = 0.2


def level_share(level):
    """The fraction of the fleet's throughput a load level stands for."""
    return ALL_LOAD_LEVELS[level]


def load_scale_for(level, city_arrival):
    """Multiplier that turns the generated day into the requested load level.

    `city_arrival` is the waste per step the scenarios produce before any
    scaling (see `scenario.city_arrival_rate`). With this multiplier the
    city really produces `level * FLEET_THROUGHPUT` units per step.

    The scenario is generated once and only read; the multiplier is applied
    while the model reads the arrivals, so both strategies always face the
    exact same waste at the same step.
    """
    return level * FLEET_THROUGHPUT / city_arrival


# --------------------------------------------------------------------------
# Experiment
# --------------------------------------------------------------------------
HORIZON = 300  # steps in one simulated day (10 days' worth of clock time)
N_SCENARIOS = 20
BASE_SEED = 1000