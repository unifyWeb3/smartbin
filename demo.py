"""Small scripted checks, one per build stage.

    python demo.py 1   # the rules of a bin, and one bin with one truck
    python demo.py 2   # one truck + dispatcher, partial pickup
    python demo.py 3   # three trucks, cancelled jobs, tie-breaking
    python demo.py 4   # one scenario replayed under both strategies
    python demo.py all # all four

Each check prints a readable trace so the behaviour can be followed by eye,
and stops with an error message if a rule does not hold.
"""

import sys

import config as C
from agents import BinStatus, CollectorState, manhattan
from model import BaselineModel, SmartModel
from scenario import city_arrival_rate, generate_scenario

# A small, slow day: one bin, steady waste input, no fires.
# The horizon is long enough for the truck to make several round trips.
DEMO_SCENARIO = dict(n_bins=1, horizon=70, incident_chance=0.0, arrival_range=(0.6, 0.8))


def bin_state_line(model, bin_agent):
    """One line describing a single bin at the current step."""
    request = "request open" if bin_agent.has_request else "-"
    alert = "SAFETY" if bin_agent.alert_active else "-"
    queue = "*" if bin_agent in model.dispatcher.queue else " "
    return (
        f"t={model.now:2d} | bin {bin_agent.unique_id} "
        f"level {bin_agent.level:6.1f} {bin_agent.status.value:<11} "
        f"temp {bin_agent.temperature:5.1f} | {request:<12} {alert:<6} {queue} | "
        f"{model.collectors[0]}"
    )


def check_bin_rules():
    """The rules of BinAgent on their own, without any truck in the way."""
    print("=" * 78)
    print("STAGE 1a - the rules of a single bin")
    print("=" * 78)
    model = SmartModel(generate_scenario(seed=7, **DEMO_SCENARIO), n_collectors=1)
    b = model.bins[0]
    b.level = 0.0

    # 1. the four statuses follow the fill level
    for waste, expected in [(69, BinStatus.NORMAL), (1, BinStatus.NEARLY_FULL),
                            (19, BinStatus.NEARLY_FULL), (1, BinStatus.REQUESTING),
                            (10, BinStatus.FULL)]:
        b.add_waste(waste)
        print(f"  add {waste:3.0f} -> level {b.level:5.1f} -> {b.status.value}")
        assert b.status is expected, f"expected {expected} at {b.level}"

    # 2. only one request per bin, even after asking again and again
    assert b.has_request, "crossing the threshold must raise a request"
    assert b.request_collection() is False, "a second request must be refused"
    print(f"  one request per bin: has_request={b.has_request}, "
          f"a second request was refused")
    print(f"  reaching 100 % counted overflow episode #{b.overflow_episodes}\n")
    assert b.overflow_episodes == 1, "reaching 100 % counts one episode"

    # 3. an overflow episode is counted once, not on every step
    b.add_waste(30)  # the bin is full, this waste stays on the street
    b.add_waste(30)
    b.add_waste(30)
    print(f"  90 more units on an already full bin: overflow episodes still "
          f"{b.overflow_episodes}, uncontained waste {b.uncontained_waste:.0f} units")
    assert b.overflow_episodes == 1, "an overflow must be counted only once"
    assert b.uncontained_waste == 90.0, "waste that does not fit must be counted"

    # 4. a collection resets the bin, and a new spill is a new episode
    b.collect(20)
    print(f"  after a 20 unit pickup: level {b.level:.1f}, "
          f"status {b.status.value}, request closed={not b.has_request}")
    assert b.level == 80 and not b.has_request
    b.add_waste(20)
    assert b.overflow_episodes == 2, "a new spill after a collection is a new episode"
    print(f"  after refilling to 100 again: {b.overflow_episodes} overflow episodes\n")

    # 5. a partial pickup that is not enough leaves the request open
    b.level = 95.0
    b.has_request = False
    b.request_collection()
    b.collect(5)  # only 95 - 5 = 90 units left, still at the threshold
    print(f"  partial pickup of 5 out of 95: level {b.level:.1f}, "
          f"request still open={b.has_request}")
    assert b.has_request, "at 90 units the request must stay open"

    # 6. the safety alert is a separate state with its own timer
    b.update_temperature(20.0)
    assert not b.alert_active, "a cool bin has no alert"
    b.update_temperature(60.0)
    assert b.alert_active, "a hot bin must raise an alert"
    assert b.has_request, "the safety alert must not touch the collection request"
    print(f"  bin at 60 C: alert active={b.alert_active}, collection request={b.has_request} "
          f"(two independent states)")
    for step in range(C.ALERT_DURATION):
        model.now += 1
        b.update_temperature(60.0)  # still burning
    print(f"  after {C.ALERT_DURATION} steps the alert clears on its own: "
          f"alert active={b.alert_active}")
    assert not b.alert_active, f"the alert must clear after {C.ALERT_DURATION} steps"
    print("\nPASS: thresholds, single request, overflow episodes, partial pickup, "
          "independent safety alert")


def stage1_single_bin():
    """One bin, one truck, no dispatcher decisions to check yet."""
    print()
    print("=" * 78)
    print("STAGE 1 - one bin fills up, requests a collection, gets emptied")
    print("=" * 78)
    scenario = generate_scenario(seed=7, **DEMO_SCENARIO)
    print(f"scenario seed {scenario.seed}, bin at {scenario.bin_positions[0]} "
          f"starting at {scenario.initial_levels[0]} units, "
          f"{scenario.horizon} steps, {scenario.arrivals[0][0]:.1f} units arriving per step\n")

    model = SmartModel(scenario, n_collectors=1)
    bin_agent = model.bins[0]
    collector = model.collectors[0]
    checks = []

    model = SmartModel(scenario, n_collectors=1)
    bin_agent = model.bins[0]
    requests_raised = 0

    while not model.is_done:
        was_requesting = bin_agent.has_request

        model.step()
        print(bin_state_line(model, bin_agent))

        if bin_agent.has_request and not was_requesting:
            requests_raised += 1
            print(f"     -> bin crossed {C.COLLECTION_THRESHOLD:.0f} units and asked "
                  f"for a collection at step {bin_agent.request_step}")

        if was_requesting and not bin_agent.has_request:
            print(f"     -> the request was closed by the pickup, bin is back to "
                  f"{bin_agent.level:.1f} units ({bin_agent.status.value})")

    pickups = model.metrics.collection_log
    print("\ncollection log:")
    for row in pickups:
        print("   ", row)

    # ---- the checks ------------------------------------------------------
    assert len(pickups) >= 2, "expected at least two fill/collect cycles"

    for row in pickups:
        if not row["still_requesting"]:
            assert row["level_after"] < C.COLLECTION_THRESHOLD, \
                "a closed request must leave the bin below the threshold"
            assert row["waited"] is not None, "a closed request must report a waiting time"

    # One request per bin: every request that was raised is either served or
    # still open, never both and never twice.
    assert requests_raised == model.metrics.completed_pickups + int(bin_agent.has_request), \
        "every raised request must be either served or still open"

    checks.append(f"{requests_raised} requests raised, one per fill-up, no duplicates")
    checks.append(f"{len(pickups)} pickups, every closed request reset the bin below "
                  f"{C.COLLECTION_THRESHOLD:.0f} units")
    checks.append(f"{bin_agent.overflow_episodes} overflow episodes, each counted once")
    checks.append(f"waiting times: {[r['waited'] for r in pickups]}")
    print("\nPASS: " + "; ".join(checks))


def queue_line(model):
    """The dispatcher queue, most urgent bin first, as one short string.

    Only bins that asked for a collection *and* have no truck on the way are
    in the queue, so a bin being served right now does not show up here.
    """
    if not model.dispatcher.queue:
        return "queue empty"
    return " > ".join(f"bin{b.unique_id}({b.level:.0f})" for b in model.dispatcher.queue)


def log_events(model, targets_before, pickups_before):
    """Print what the dispatcher and the pickups did during the last step.

    Returns the state to hand back into the next call.
    """
    for collector in model.collectors:
        if collector.target is not None and collector.target is not targets_before[collector]:
            target = collector.target
            print(f"     -> dispatcher sends {collector.name} to bin {target.unique_id} "
                  f"({target.level:.0f} units)")

    for row in model.metrics.collection_log[pickups_before:]:
        tail = ("still above the threshold, the request stays open"
                if row["still_requesting"]
                else "back below the threshold, the request is closed")
        print(f"     -> pickup: {row['units']:.0f} units, bin is at {row['level_after']:.0f}, "
              f"{tail}")

    return {c: c.target for c in model.collectors}, len(model.metrics.collection_log)


def stage2_dispatcher_priority():
    """Part A: three bins ask at once, the fullest one is served first."""
    print()
    print("=" * 78)
    print("STAGE 2a - the dispatcher serves the fullest bin first")
    print("=" * 78)

    scenario = generate_scenario(seed=11, n_bins=3, horizon=14, incident_chance=0.0)
    model = SmartModel(scenario, n_collectors=1)

    # Put the three bins at three different fill levels, all of them asking
    # for a collection. No new waste arrives during this check.
    levels = [95.0, 100.0, 92.0]
    for bin_agent, level in zip(model.bins, levels):
        bin_agent.level = level
        bin_agent.request_collection()
        print(f"  bin {bin_agent.unique_id} at {bin_agent.pos} starts at {level:.0f} units "
              f"with an open request")

    model.dispatcher.refresh_queue()
    first_in_queue = queue_line(model)
    print(f"\n  queue order: {first_in_queue}\n")

    targets, pickups = {c: c.target for c in model.collectors}, 0
    while not model.metrics.collection_log:
        model.step()
        print(f"t={model.now:2d} | {queue_line(model):<24} | {model.collectors[0]}")
        targets, pickups = log_events(model, targets, pickups)

    pickup = model.metrics.collection_log[0]
    print(f"\n  first pickup: bin {pickup['bin']} took {pickup['units']:.0f} units, "
          f"bin is back to {pickup['level_after']:.0f}")

    assert pickup["bin"] == model.bins[1].unique_id, "the fullest bin must be served first"
    assert model.metrics.pickups == 1
    print(f"\nPASS: the queue was sorted {first_in_queue}, so the truck went to the fullest "
          f"bin (bin {pickup['bin']} at 100 units) and the other two kept waiting")


def stage2_partial_pickup():
    """Part B: the truck takes what fits, the bin stays requested, same bin again."""
    print()
    print("=" * 78)
    print("STAGE 2b - partial pickup and the same bin being reassigned")
    print("=" * 78)

    scenario = generate_scenario(seed=11, n_bins=1, horizon=14, incident_chance=0.0)
    scenario.arrivals = [[0.0] for _ in range(scenario.horizon)]  # no new waste
    model = SmartModel(scenario, n_collectors=1)

    bin_agent = model.bins[0]
    collector = model.collectors[0]
    bin_agent.level = bin_agent.capacity  # a completely full bin
    bin_agent.request_collection()
    collector.load = 10.0  # the truck is already half full from an earlier bin
    collector.trip_bins = 1

    print(f"  bin at {bin_agent.pos} holds {bin_agent.level:.0f} units and asks for a collection")
    print(f"  the truck carries {collector.load:.0f} of its {collector.capacity:.0f} units, "
          f"so only {collector.capacity - collector.load:.0f} units of room are left")
    print("  reading the log: the queue only lists bins that asked and have no truck "
          "on the way\n")

    targets, pickups = {collector: collector.target}, 0
    while not model.is_done:
        model.step()
        print(f"t={model.now:2d} | bin {bin_agent.level:5.1f} "
              f"{bin_agent.status.value:<11} request={str(bin_agent.has_request):<5} | "
              f"{queue_line(model):<16} | {collector}")
        targets, pickups = log_events(model, targets, pickups)

    print("\n  collection log:")
    for row in model.metrics.collection_log:
        print(f"    step {row['step']:2d}: took {row['units']:.0f} units, bin is back to "
              f"{row['level_after']:.0f}, request still open={row['still_requesting']}")

    first, second = model.metrics.collection_log
    checks = []

    # the truck only took what fitted
    assert first["units"] == collector.capacity - 10.0, "the truck must only take what fits"
    assert first["level_after"] == bin_agent.capacity - first["units"]
    checks.append(f"truck had {collector.capacity - 10.0:.0f} units of room and took "
                  f"exactly that, leaving {first['level_after']:.0f} units in the bin")

    # the request stays open while the bin is still at or above 90
    assert first["still_requesting"], "a partial pickup must leave the request open"
    assert first["level_after"] >= C.COLLECTION_THRESHOLD
    checks.append(f"bin stayed at {first['level_after']:.0f} units "
                  f"(>= {C.COLLECTION_THRESHOLD:.0f}), so the request stayed open")

    # the dispatcher sends the same bin back to the same truck
    assert second["bin"] == first["bin"], "the dispatcher must reassign the same bin"
    assert second["truck"] == first["truck"]
    checks.append(f"after unloading at the depot the dispatcher reassigned the same bin "
                  f"({first['truck']} -> bin {second['bin']})")

    # and the second pickup finally brings it below the threshold
    assert second["units"] == collector.capacity, "the empty truck takes a full load"
    assert not second["still_requesting"], "the request must close below 90 units"
    checks.append(f"the empty truck then took {second['units']:.0f} units, the bin fell to "
                  f"{second['level_after']:.0f} and the request closed")

    print("\nPASS: " + "; ".join(checks))


# Six bins and three trucks, all close to the depot so the logs stay short.
STAGE3_SEED = 17
STAGE3_LEVELS = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0]
# The three trucks start the day in three different corners, so that the
# "nearest truck" rule has something to choose between.
STAGE3_TRUCK_STARTS = [(16, 16), (10, 10), (4, 4)]


def stage3_setup(levels=STAGE3_LEVELS, truck_starts=STAGE3_TRUCK_STARTS, n_collectors=3,
                 arrivals=None, horizon=30):
    """A ready made situation: six bins, all of them asking for a collection."""
    scenario = generate_scenario(
        seed=STAGE3_SEED, n_bins=len(levels), horizon=horizon, incident_chance=0.0
    )
    if arrivals is None:  # no new waste, so the levels stay exactly as set
        scenario.arrivals = [[0.0] * len(levels) for _ in range(horizon)]
    model = SmartModel(scenario, n_collectors=n_collectors)

    for bin_agent, level in zip(model.bins, levels):
        bin_agent.level = level
        bin_agent.request_collection()
    for collector, start in zip(model.collectors, truck_starts or []):
        collector.pos = start
    return model


def trucks_line(model):
    """The three trucks on one short line."""
    return " | ".join(
        f"{c.name.split()[1]} {c.state.value:<9} @{str(c.pos):<7} {c.load:3.0f}/{c.capacity:.0f}"
        + (f" -> bin{c.target.unique_id}" if c.target is not None else "")
        for c in model.collectors
    )


def assert_no_double_assignment(model):
    """No bin has two trucks on the way, and no truck has two bins."""
    targets = [c.target for c in model.collectors if c.target is not None]
    assert len(targets) == len(set(targets)), "two trucks were sent to the same bin"

    for collector in model.collectors:
        if collector.target is not None:
            assert collector.target.served_by is collector, \
                f"bin {collector.target.unique_id} does not know about its truck"

    waiting = [b.served_by for b in model.bins if b.served_by is not None]
    assert len(waiting) == len(set(waiting)), "two bins were given the same truck"


def stage3_assignment_round():
    """Six bins ask at once: the fullest first, each to the nearest free truck."""
    print("=" * 78)
    print("STAGE 3a - one assignment round: fullest bin first, nearest free truck")
    print("=" * 78)

    model = stage3_setup()
    for bin_agent in model.bins:
        print(f"  bin {bin_agent.unique_id} at {str(bin_agent.pos):<7} "
              f"holds {bin_agent.level:.0f} units and asks for a collection")
    print(f"  trucks start at {[c.pos for c in model.collectors]}")

    model.dispatcher.refresh_queue()
    queue = list(model.dispatcher.queue)
    print(f"\n  dispatcher queue (fullest first): {queue_line(model)}")

    # Distance from every truck to every bin, to show what "nearest" means.
    print("\n  distances to each bin:")
    for bin_agent in model.bins:
        distances = "  ".join(
            f"{c.name.split()[1]}={manhattan(c.pos, bin_agent.pos):2d}" for c in model.collectors
        )
        print(f"    bin {bin_agent.unique_id}: {distances}")

    # Re-derive the expected assignment with the documented rule, so the demo
    # checks the dispatcher against the rule rather than against itself.
    free = list(model.collectors)
    expected = []
    for bin_agent in queue:  # already sorted fullest first
        if not free:
            break  # every truck has a job, the rest of the bins keep waiting
        best = min(free, key=lambda c: (manhattan(c.pos, bin_agent.pos), c.index))
        expected.append((bin_agent, best))
        free.remove(best)

    model.step()
    actual = sorted(
        [(c.target, c) for c in model.collectors if c.target is not None],
        key=lambda pair: pair[0].unique_id,
    )
    print(f"\n  assignment after one step:")
    for bin_agent, collector in actual:
        print(f"    bin {bin_agent.unique_id} ({bin_agent.level:.0f} units) -> {collector.name}")

    assert actual == sorted(expected, key=lambda pair: pair[0].unique_id), \
        "the dispatcher must serve the fullest bin first, each with the nearest free truck"
    assert len(actual) == 3, "three free trucks must serve three different bins"
    assert model.dispatcher.queue, "the remaining bins must stay in the queue"
    print(f"\n  still waiting: {queue_line(model)}")

    print(f"\nPASS: the three fullest bins (100, 98, 96) got the three trucks, each to the "
          f"nearest free one; bins 4, 5 and 6 stayed in the queue")


def stage3_three_trucks():
    """Three trucks work in parallel for a whole day."""
    print()
    print("=" * 78)
    print("STAGE 3b - three trucks working at the same time")
    print("=" * 78)

    # A busy day, so the trucks keep receiving new work.
    model = stage3_setup(levels=[100.0] * 6, truck_starts=None,
                         arrivals=[2.5] * 6, horizon=30)
    print(f"  six bins, three trucks, {model.horizon} steps, "
          f"2.5 units of new waste per bin per step\n")

    # Count every request as it is raised: the ones set up before the run plus
    # every bin that crosses the threshold again later on.
    asking = [True] * len(model.bins)
    requests_raised = sum(asking)

    targets, pickups = {c: c.target for c in model.collectors}, 0
    while not model.is_done:
        model.step()
        assert_no_double_assignment(model)  # checked on every single step

        for index, bin_agent in enumerate(model.bins):
            if bin_agent.has_request and not asking[index]:
                requests_raised += 1
            asking[index] = bin_agent.has_request

        raised = sum(asking)
        print(f"t={model.now:2d} | bins asking {raised} | {queue_line(model)}")
        print(f"     {trucks_line(model)}")
        targets, pickups = log_events(model, targets, pickups)

        for collector in model.collectors:
            if collector.abort_reason:
                print(f"     -> {collector.name} released its job: {collector.abort_reason}")
                collector.abort_reason = None

    still_open = sum(1 for b in model.bins if b.has_request)
    completed = model.metrics.completed_pickups
    print(f"\n  requests raised: {requests_raised}")
    print(f"  requests closed by a pickup: {completed}")
    print(f"  requests still open at the end: {still_open}")
    print(f"  metrics: {model.metrics.summary()}")

    assert requests_raised == completed + still_open, \
        f"{requests_raised} requests raised but {completed} served and {still_open} still open"

    print(f"\nPASS: three trucks worked in parallel without ever sharing a bin "
          f"({model.metrics.total_distance} cells driven), and every request was either "
          f"served ({completed}) or is still open ({still_open})")


def stage3_cancelled_pickup():
    """A bin is emptied while a truck is driving to it: the truck is released."""
    print()
    print("=" * 78)
    print("STAGE 3c - a job that is cancelled before the truck arrives")
    print("=" * 78)

    model = stage3_setup(horizon=12)
    for _ in range(2):  # let the trucks drive off, but not arrive yet
        model.step()
    print(f"  on the road: " + ", ".join(
        f"{c.name} -> bin {c.target.unique_id}" for c in model.collectors if c.target))

    # Somebody else empties the bin that one truck is driving to.
    victim = next(c for c in model.collectors if c.target is not None and c.index == 1)
    bin_agent = victim.target
    assert victim.state is CollectorState.TO_BIN, "the truck must still be on its way"
    cells_left = victim.distance_to_bin()
    print(f"\n  {victim.name} is still {cells_left} cell{'' if cells_left == 1 else 's'} away "
          f"when bin {bin_agent.unique_id} is emptied by somebody else")
    bin_agent.collect(bin_agent.level - 80.0)  # down to 80, below the threshold
    print(f"  bin {bin_agent.unique_id} is now at {bin_agent.level:.0f} units, "
          f"request closed={not bin_agent.has_request}")

    pickups_before = len(model.metrics.collection_log)
    model.step()
    assert_no_double_assignment(model)

    print(f"\n  after the next step:")
    print(f"    {victim.name} state={victim.state.value}, target="
          f"{victim.target.unique_id if victim.target else None}, "
          f"reason={victim.abort_reason!r}")
    print(f"    {trucks_line(model)}")

    assert victim.target is None or victim.target is not bin_agent, \
        "the truck must let go of the bin that no longer needs it"
    assert victim.abort_reason == "target bin no longer needs a collection"
    assert victim.is_free, "the released truck must be free again"
    assert bin_agent.served_by is None, "the released bin must be free again"
    victim.abort_reason = None

    # and the dispatcher gives it something else to do
    before = model.metrics.collection_log
    for _ in range(8):
        model.step()
        assert_no_double_assignment(model)
        print(f"t={model.now:2d} | {queue_line(model)}")
        print(f"     {trucks_line(model)}")

    new_pickups = model.metrics.collection_log[pickups_before:]
    assert all(row["units"] > 0 for row in new_pickups), \
        "no pickup may collect nothing"
    assert not any(row["bin"] == bin_agent.unique_id and row["units"] == 0 for row in new_pickups)
    print(f"\n  pickups after the cancellation: "
          + ", ".join(f"bin {r['bin']} took {r['units']:.0f}" for r in new_pickups))

    print(f"\nPASS: the truck was released instead of driving on, was reassigned to other "
          f"bins, and no pickup collected nothing")


def check_tie_breaking():
    """Equal levels and equal distances are broken by id, always the same way."""
    print()
    print("=" * 78)
    print("STAGE 3d - tie-breaking is decided by id, never by chance")
    print("=" * 78)

    # All bins hold exactly the same level and all three trucks stand at the
    # depot, so every truck is at exactly the same distance from every bin.
    model = stage3_setup(levels=[95.0, 95.0], truck_starts=None, horizon=6)
    print(f"  bin 1 holds {model.bins[0].level:.0f} units, bin 2 holds "
          f"{model.bins[1].level:.0f} units, all three trucks are at the depot {C.DEPOT_POS}")
    for bin_agent in model.bins:
        distances = ", ".join(
            f"{c.name.split()[1]}={manhattan(c.pos, bin_agent.pos)}" for c in model.collectors
        )
        print(f"  bin {bin_agent.unique_id} at {str(bin_agent.pos):<7}: distance {distances}")
    print("  every distance is a tie, so only the ids can break it")

    model.dispatcher.refresh_queue()
    print(f"\n  queue order: {queue_line(model)}")
    assert model.dispatcher.queue[0] is model.bins[0], \
        "on equal levels the lower bin id must go first"

    model.step()
    served = sorted((c.target.unique_id, c.index) for c in model.collectors if c.target)
    print(f"  assignment: {[(f'bin{b}', f'truck {i + 1}') for b, i in served]}")

    assert served[0][1] == 0, "on equal distance the lower truck id must win"
    assert model.bins[0].served_by.index == 0 and model.bins[1].served_by.index == 1

    # and the same scenario gives the same answer every time
    again = stage3_setup(levels=[95.0, 95.0], truck_starts=None, horizon=6)
    again.step()
    repeat = sorted((c.target.unique_id, c.index) for c in again.collectors if c.target)
    assert repeat == served, "the same scenario must give the same assignment"

    print(f"\nPASS: equal levels go to the lower bin id, equal distances to the lower truck "
          f"id, and repeating the run gives the identical assignment")


def stage4_same_scenario_both_strategies():
    """One and the same scenario, replayed under both strategies."""
    print()
    print("=" * 78)
    print("STAGE 4 - the same scenario replayed under both strategies")
    print("=" * 78)

    scenario = generate_scenario(1000)
    fresh = generate_scenario(1000)  # the same seed must give the same day

    total_waste = sum(sum(step) for step in scenario.arrivals)
    city_arrival = city_arrival_rate([scenario])
    print(f"  scenario seed {scenario.seed}, {len(scenario.bin_positions)} bins, "
          f"{scenario.horizon} steps")
    print(f"  nominal waste for the whole day: {total_waste:,.0f} units "
          f"({city_arrival:.2f} per step)")

    load_scale = C.load_scale_for(C.LOAD_LEVELS["high"], city_arrival)
    print(f"  replayed at load level 'high' (scale {load_scale:.3f} -> "
          f"{city_arrival * load_scale:.2f} units per step, "
          f"which is {C.LOAD_LEVELS['high']:.0%} of what the fleet can collect)\n")

    results = {}
    for name, model_class in [("baseline", BaselineModel), ("smart", SmartModel)]:
        model = model_class(scenario, load_scale=load_scale)
        print(f"  --- {name} ---")
        while not model.is_done:
            model.step()
            if model.now % 60 == 0:  # one line every 60 steps, not every step
                waiting = sum(1 for b in model.bins if b.has_request)
                full = sum(1 for b in model.bins if b.level >= b.capacity)
                print(f"    step {model.now:3d} | bins asking {waiting:2d} | full {full:2d} | "
                      f"driven {model.metrics.total_distance:5d} cells | "
                      f"pickups {model.metrics.pickups:3d} | "
                      f"uncontained {sum(b.uncontained_waste for b in model.bins):7.1f}")
        summary = model.metrics.summary()
        results[name] = summary
        print(f"    done: {summary}\n")

    # --- the checks -------------------------------------------------------
    # 1. the scenario is only read, never written to
    assert scenario == fresh, "the scenario was changed by a run"
    print("  the scenario object is byte for byte the same after both runs "
          "(checked against a freshly generated one from the same seed)")

    # 2. every request was either served or is still open
    for name, summary in results.items():
        assert (
            summary["requests_raised"]
            == summary["completed_pickups"] + summary["unserved_requests"]
        ), f"{name}: requests do not add up"
        print(f"  {name}: {summary['requests_raised']} requests raised = "
              f"{summary['completed_pickups']} served + {summary['unserved_requests']} still open")

    # 3. both strategies really did see the same waste
    print("\n  both strategies drove the same city:")
    print(f"    baseline: {results['baseline']['total_distance']:,} cells, "
          f"{results['baseline']['pickups']} pickups")
    print(f"    smart:    {results['smart']['total_distance']:,} cells, "
          f"{results['smart']['pickups']} pickups")
    print(f"\nPASS: one scenario, two strategies, identical input")


STAGES = {
    1: [check_bin_rules, stage1_single_bin],
    2: [stage2_dispatcher_priority, stage2_partial_pickup],
    3: [stage3_assignment_round, stage3_three_trucks, stage3_cancelled_pickup,
        check_tie_breaking],
    4: [stage4_same_scenario_both_strategies],
}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    stages = STAGES if which == "all" else {int(which): STAGES[int(which)]}
    for checks in stages.values():
        for check in checks:
            check()