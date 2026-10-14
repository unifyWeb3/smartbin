"""The city model and the metrics it records.

`CityModel` owns the grid, creates the agents and decides the order of
things inside one step. There is no scheduler: the model walks through
`self.agents` itself.
"""

import math

import mesa

import config as C
from agents import BinAgent, CollectorAgent, DispatcherAgent, manhattan


class Metrics:
    """Everything we measure during a run.

    The model and the agents call the `record_*` methods, so all numbers of
    a run end up in one place and can be printed or written to a file.
    """

    def __init__(self, model):
        self.model = model
        self.total_distance = 0  # cells driven by all trucks
        self.waiting_sum = 0.0  # steps waited, over all closed requests
        self.max_waiting = 0  # longest wait of a single closed request
        self.completed_pickups = 0  # requests that were actually served
        self.pickups = 0  # every pickup, also partial ones
        self.trips = []  # bins emptied per trip, one entry per trip
        self.collection_log = []  # one row per pickup, for the dashboard
        self.waste_offered = 0.0  # waste that dropped into the city in total
        # per step counters, filled in by end_step()
        self.queue_lengths = []  # how many bins waited for a truck, per step
        self.units_per_step = []  # units picked up in each step
        self.peak_units_in_step = 0.0  # best pickup rate of a single step
        self._units_this_step = 0.0

    def add_distance(self, cells):
        self.total_distance += cells

    def add_offer(self, units):
        """Waste that dropped into the city during this step."""
        self.waste_offered += units

    def end_step(self):
        """Close off one model step and keep the numbers of that step.

        Every strategy leaves `dispatcher.queue` up to date, so the queue
        length can be measured the same way for all of them.
        """
        self.queue_lengths.append(len(self.model.dispatcher.queue))
        self.units_per_step.append(self._units_this_step)
        self.peak_units_in_step = max(self.peak_units_in_step, self._units_this_step)
        self._units_this_step = 0.0

    def best_sustained_rate(self):
        """Highest average pickup rate over any stretch of one round trip.

        The busiest single step says nothing about what the fleet can keep
        up with: when all trucks happen to finish loading at once it can be
        three times the formula. A window of one round trip is the same span
        of time the throughput formula talks about, so the two can be
        compared honestly.
        """
        window = int(C.ROUND_TRIP_STEPS)
        if len(self.units_per_step) < window:
            window = len(self.units_per_step)
        if not window:
            return 0.0
        return max(
            sum(self.units_per_step[start:start + window]) / window
            for start in range(len(self.units_per_step) - window + 1)
        )

    def record_pickup(self, collector, bin_agent, units, waited):
        """A truck took `units` out of `bin_agent`.

        `waited` is None when the bin did not have an open request, or when
        the request is still open after this (partial) pickup.
        """
        self.pickups += 1
        self._units_this_step += units
        if waited is not None:
            self.waiting_sum += waited
            self.completed_pickups += 1
            self.max_waiting = max(self.max_waiting, waited)
        self.collection_log.append(
            {
                "step": self.model.now,
                "truck": collector.name,
                "bin": bin_agent.unique_id,
                "units": units,
                "level_after": bin_agent.level,
                "still_requesting": bin_agent.has_request,
                "waited": waited,
            }
        )

    @property
    def units_collected(self):
        """Total waste taken out of the bins during the run."""
        return sum(self.units_per_step)

    def record_trip(self, bins_on_trip):
        """A truck unloaded at the depot."""
        self.trips.append(bins_on_trip)

    # -- results ---------------------------------------------------------

    def mass_balance(self):
        """Where every unit of offered waste ended up.

        The book-keeping identity of the whole model:

            offered = collected + uncontained + (bins at the end - bins at the start)

        Nothing may be lost and nothing may appear. The tolerance is small
        because bin levels are stored rounded to two decimals.
        """
        collected = sum(self.units_per_step)
        uncontained = sum(b.uncontained_waste for b in self.model.bins)
        net_change = sum(b.level for b in self.model.bins) - self.model.initial_total
        return {
            "offered": self.waste_offered,
            "collected": collected,
            "uncontained": uncontained,
            "net_change": net_change,
            "residual": self.waste_offered - (collected + uncontained + net_change),
        }

    def summary(self):
        """The numbers reported for a finished run."""
        waiting = [b for b in self.model.bins if b.has_request]
        steps = self.model.horizon or 1
        return {
            "overflow_episodes": sum(b.overflow_episodes for b in self.model.bins),
            "uncontained_waste": round(sum(b.uncontained_waste for b in self.model.bins), 2),
            "total_distance": self.total_distance,
            "avg_waiting_completed": round(self.waiting_sum / self.completed_pickups, 2)
            if self.completed_pickups
            else None,
            "completed_pickups": self.completed_pickups,
            "requests_raised": sum(b.requests_raised for b in self.model.bins),
            "unserved_requests": len(waiting),
            # how far from the depot the bins that are still waiting sit, to
            # see whether a strategy leaves the far corners behind
            "unserved_mean_distance": round(
                sum(manhattan(b.pos, C.DEPOT_POS) for b in waiting) / len(waiting), 2
            )
            if waiting
            else 0.0,
            "avg_bins_per_trip": round(sum(self.trips) / len(self.trips), 2)
            if self.trips
            else None,
            "trips": len(self.trips),
            "pickups": self.pickups,
            # --- added in stage 4b ---
            # the worst wait of a request that was answered, and the worst
            # wait of a request that is still open at the end of the day
            "max_waiting_completed": self.max_waiting,
            "max_open_wait": max(
                (self.model.now - b.request_step for b in waiting), default=0
            ),
            "full_bins_at_end": sum(1 for b in self.model.bins if b.level >= b.capacity),
            "truck_utilisation": round(
                sum(c.utilisation for c in self.model.collectors) / len(self.model.collectors), 4
            ),
            "mean_queue_length": round(sum(self.queue_lengths) / len(self.queue_lengths), 2)
            if self.queue_lengths
            else 0.0,
            "units_collected": round(self.units_collected, 2),
            "mean_units_per_step": round(self.units_collected / steps, 4),
            "peak_units_in_step": round(self.peak_units_in_step, 2),
            "best_sustained_rate": round(self.best_sustained_rate(), 4),
            # --- added in stage 4c: the waste book-keeping ---
            "waste_offered": round(self.waste_offered, 2),
            "net_bin_fill_change": round(
                sum(b.level for b in self.model.bins) - self.model.initial_total, 2
            ),
        }


class CityModel(mesa.Model):
    """A city with bins, trucks and a dispatcher.

    One step of the model happens in this order:

    1. the scenario drops waste into the bins,
    2. the scenario temperatures are applied to the bins (safety alerts),
    3. the strategy decides who drives where,
    4. the trucks move and load,
    5. the step counter goes up.

    Because the scenario is only read and never generated here, running the
    same scenario twice always gives exactly the same run.
    """

    def __init__(self, scenario, n_collectors=C.N_COLLECTORS, strategy="smart", load_scale=1.0):
        super().__init__()
        self.scenario = scenario
        self.horizon = scenario.horizon
        self.strategy = strategy
        self.load_scale = load_scale  # how busy the city is, see config.py

        # what the bins held before the first drop of waste, for the
        # mass balance at the end of the run
        self.initial_total = sum(scenario.initial_levels)

        # `now` is the index of the scenario step that is being simulated.
        # (Mesa's own `self.steps` counter runs one ahead of that, so the
        # agents ask the model for `now` instead.)
        self.now = 0

        self.grid = mesa.space.MultiGrid(C.GRID_WIDTH, C.GRID_HEIGHT, False)

        self.bins = []
        for index, pos in enumerate(scenario.bin_positions):
            bin_agent = BinAgent(self, scenario.initial_levels[index])
            self.grid.place_agent(bin_agent, pos)  # this also sets bin_agent.pos
            self.bins.append(bin_agent)

        self.collectors = [CollectorAgent(self, i) for i in range(n_collectors)]
        for collector in self.collectors:
            self.grid.place_agent(collector, C.DEPOT_POS)

        self.dispatcher = DispatcherAgent(self)
        self.grid.place_agent(self.dispatcher, C.DEPOT_POS)

        self.metrics = Metrics(self)

    # -- main loop -------------------------------------------------------

    @property
    def is_done(self):
        return self.now >= self.horizon

    @property
    def has_pending_work(self):
        """Is there a bin that wants a truck and is safe to empty?"""
        return any(b.can_be_served and b.served_by is None for b in self.bins)

    def step(self):
        if self.is_done:
            return
        self.apply_scenario()
        self.assign_work()  # every strategy leaves dispatcher.queue up to date
        for collector in self.collectors:
            collector.step()
        self.metrics.end_step()
        self.now += 1

    def apply_scenario(self):
        """Add today's waste and today's temperature to every bin.

        The waste is the scenario's nominal amount multiplied by the load
        level. The scenario itself is never written to, so both strategies
        read exactly the same waste at exactly the same step. Waste that no
        longer fits is counted by the bin itself.
        """
        arrivals = self.scenario.arrivals[self.now]
        temperatures = self.scenario.temperatures[self.now]
        for index, bin_agent in enumerate(self.bins):
            waste = arrivals[index] * self.load_scale
            self.metrics.add_offer(waste)
            bin_agent.add_waste(waste)
            bin_agent.update_temperature(temperatures[index])

    def assign_work(self):
        """Strategy hook: decide who drives where. Overridden per strategy."""
        raise NotImplementedError

    def run(self):
        """Run the whole day and return the metrics."""
        while not self.is_done:
            self.step()
        return self.metrics.summary()


class SmartModel(CityModel):
    """Strategy 2: the dispatcher decides.

    Every step the dispatcher takes the bins that need to be emptied, orders
    them from the fullest to the emptiest and sends the nearest free truck
    to each of them.
    """

    def __init__(self, scenario, n_collectors=C.N_COLLECTORS, load_scale=1.0):
        super().__init__(scenario, n_collectors, strategy="smart", load_scale=load_scale)

    def assign_work(self):
        self.dispatcher.step()


def build_routes(bins, depot, n_routes):
    """Cut the bins into fixed routes, one route per truck.

    The rule has two parts and uses no random numbers, so the same city
    always produces the same routes:

    1. **Sector.** The compass around the depot is cut into `n_routes`
       equal wedges. A bin belongs to the wedge its direction from the
       depot points at.
    2. **Order inside a wedge.** The bins of one wedge are visited in
       nearest-neighbour order: starting at the depot, always drive to the
       closest bin of the wedge that is not on the list yet.
    """
    wedges = [[] for _ in range(n_routes)]
    for bin_agent in bins:
        angle = math.atan2(bin_agent.pos[1] - depot[1], bin_agent.pos[0] - depot[0])
        wedges[int((angle + math.pi) / (2 * math.pi) * n_routes) % n_routes].append(bin_agent)

    routes = []
    for wedge in wedges:
        route, current, remaining = [], depot, list(wedge)
        while remaining:
            next_bin = min(remaining, key=lambda b: (manhattan(current, b.pos), b.unique_id))
            route.append(next_bin)
            remaining.remove(next_bin)
            current = next_bin.pos
        routes.append(route)
    return routes


class BaselineModel(CityModel):
    """Strategy 1: fixed route, no dispatcher.

    Every bin belongs to exactly one truck's route. A free truck always
    takes the next bin on its own list and empties whatever it finds there,
    also from bins that never asked for a collection. When the truck is full
    it drives back to the depot, unloads and carries on with the rest of its
    route.

    The baseline never looks at a collection request. The safety rule is the
    same as for the smart strategy: a bin with a temperature alert is
    skipped and comes back on the next round of the route.
    """

    def __init__(self, scenario, n_collectors=C.N_COLLECTORS, load_scale=1.0):
        super().__init__(scenario, n_collectors, strategy="baseline", load_scale=load_scale)
        self.routes = build_routes(self.bins, C.DEPOT_POS, n_collectors)
        self.route_cursor = [0] * n_collectors  # how far along its route each truck is

    def assign_work(self):
        # The baseline measures the dispatcher queue like everybody else, it
        # just never acts on it: the route decides, not the requests.
        self.dispatcher.refresh_queue()
        for index, (collector, route) in enumerate(zip(self.collectors, self.routes)):
            if not collector.is_free or not route:
                continue
            bin_agent = route[self.route_cursor[index] % len(route)]
            self.route_cursor[index] += 1
            if bin_agent.alert_active:
                continue  # safety rule: come back for it in the next round
            collector.assign(bin_agent, on_request=False)


class NoCollectionModel(CityModel):
    """Lower bound for the experiment: nobody ever collects anything."""

    def __init__(self, scenario, n_collectors=C.N_COLLECTORS, load_scale=1.0):
        super().__init__(scenario, n_collectors, strategy="no_collection", load_scale=load_scale)

    def assign_work(self):
        self.dispatcher.refresh_queue()  # measured, but nobody acts on it
        # the trucks stay in the depot for the whole day