"""The three agents of the system: bins, collector trucks and the dispatcher.

All behaviour is rule-based - there is no machine learning anywhere.
"""

from enum import Enum

import mesa

import config as C


def manhattan(a, b):
    """Number of cells between two positions on an empty grid."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


# ==========================================================================
# Bins
# ==========================================================================


class BinStatus(Enum):
    NORMAL = "normal"  # below 70 %
    NEARLY_FULL = "nearly_full"  # 70 - 89 %
    REQUESTING = "requesting"  # 90 % or more, a collection was asked for
    FULL = "full"  # 100 %, waste is spilling out


class BinAgent(mesa.Agent):
    """A waste bin standing on a street corner.

    The bin is mostly a data holder plus a small state machine:

    1. waste drops in every step until the bin crosses the collection
       threshold, then it asks the dispatcher for a collection (one request
       per bin at a time, never two),
    2. it can separately raise a *safety alert* when it gets too hot. The
       alert has nothing to do with the collection request: a burning bin
       can still ask for an empty, it is just nobody is allowed to empty it
       while the alert is on.
    """

    def __init__(self, model, level=0.0):
        super().__init__(model)
        self.capacity = C.BIN_CAPACITY
        self.level = level
        self.temperature = C.AMBIENT_TEMP

        # collection request
        self.has_request = False
        self.request_step = None  # step the open request was raised at
        self.served_by = None  # truck currently on its way to this bin
        self.requests_raised = 0  # how often this bin asked in total

        # safety alert
        self.alert_active = False
        self.alert_step = None
        self.alert_clears_at = None

        # metrics
        self.overflow_episodes = 0
        self.uncontained_waste = 0.0
        self._was_full = level >= self.capacity

    # -- status ----------------------------------------------------------

    @property
    def status(self):
        """The four bin states, derived from the current fill level."""
        if self.level >= self.capacity:
            return BinStatus.FULL
        if self.has_request:
            return BinStatus.REQUESTING
        if self.level >= C.NEARLY_FULL_LEVEL:
            return BinStatus.NEARLY_FULL
        return BinStatus.NORMAL

    @property
    def can_be_served(self):
        """A bin is safe to empty when it wants to be empty and is not hot."""
        return self.has_request and not self.alert_active

    # -- behaviour -------------------------------------------------------

    def add_waste(self, amount):
        """Waste drops into the bin.

        Returns the amount of waste that did not fit and stays on the street.
        """
        overflow = max(0.0, amount - (self.capacity - self.level))
        self.level = min(self.capacity, self.level + amount)
        self.uncontained_waste += overflow

        if self.level >= C.COLLECTION_THRESHOLD:
            self.request_collection()

        # An overflow *episode* is counted once, on the step where the bin
        # first reaches 100 %. After a collection the bin drops below 100 %
        # again, so the next spill counts as a new episode.
        if self.level >= self.capacity and not self._was_full:
            self._was_full = True
            self.overflow_episodes += 1

        return overflow

    def request_collection(self):
        """Ask for a collection. Ignored if a request is already open."""
        if self.has_request:
            return False
        self.has_request = True
        self.requests_raised += 1
        self.request_step = self.model.now
        return True

    def collect(self, amount):
        """The truck empties `amount` units out of the bin.

        If the truck cannot empty the whole bin, the rest stays and the
        request stays open until the bin finally drops below the collection
        threshold.
        """
        amount = min(amount, self.level)
        self.level = round(self.level - amount, 2)

        if self.level >= C.COLLECTION_THRESHOLD:
            self.has_request = True  # still too full, the request stays open
        else:
            self.has_request = False
            self.request_step = None

        if self.level < self.capacity:
            self._was_full = False

        return amount

    def update_temperature(self, temperature):
        """Store the current temperature and run the safety-alert timer.

        The alert is raised the first step the bin is too hot and clears
        itself `ALERT_DURATION` steps later, even if the bin is still hot.
        """
        self.temperature = temperature

        if temperature > C.TEMP_THRESHOLD and not self.alert_active:
            self.alert_active = True
            self.alert_step = self.model.now
            self.alert_clears_at = self.model.now + C.ALERT_DURATION
        elif self.alert_active and self.model.now >= self.alert_clears_at:
            self.alert_active = False

    def __str__(self):
        return f"Bin {self.unique_id} @{self.pos} {self.level:.0f}/{self.capacity:.0f} {self.status.value}"


# ==========================================================================
# Collector trucks
# ==========================================================================


class CollectorState(Enum):
    IDLE = "idle"  # empty, waiting at the depot
    TO_BIN = "to_bin"  # driving to the assigned bin
    LOADING = "loading"  # taking waste out of the bin
    TO_DEPOT = "to_depot"  # driving back, truck full
    UNLOADING = "unloading"  # dropping the load off


class CollectorAgent(mesa.Agent):
    """A waste truck with a fixed speed and a fixed capacity.

    A truck drives to one bin, takes as much as fits, and comes back to the
    depot when it is full. If the bin holds more than the truck can carry,
    the truck takes what fits and leaves the rest.
    """

    def __init__(self, model, index):
        super().__init__(model)
        self.index = index
        self.name = f"Truck {index + 1}"
        self.capacity = C.TRUCK_CAPACITY
        self.load = 0.0
        self.state = CollectorState.IDLE
        self.target = None  # bin currently being served
        self.on_request = True  # False when this truck is on the fixed route
        self.abort_reason = None  # why the last job was given up, if it was
        self.busy_steps = 0  # steps this truck was not standing idle
        self.trip_bins = 0  # bins emptied during the current trip

    # -- helpers ---------------------------------------------------------

    @property
    def is_free(self):
        """A free truck is idle with nothing to do (it may still be loaded)."""
        return self.state is CollectorState.IDLE and self.target is None

    def distance_to_bin(self):
        """How many cells are left to drive to the current target."""
        if self.target is None:
            return 0
        return manhattan(self.pos, self.target.pos)

    @property
    def utilisation(self):
        """Share of the day this truck spent not standing idle.

        A step counts as busy when the truck is not idle at the start of it,
        so a step in which the truck leaves the depot still counts as idle.
        """
        if not self.model.horizon:
            return 0.0
        return self.busy_steps / self.model.horizon

    def assign(self, bin_agent, on_request=True):
        """The dispatcher sends this truck to this bin.

        `on_request` is False for the fixed baseline route: there the truck
        visits every bin on the schedule and empties whatever it finds, also
        from a bin that never asked for a collection.
        """
        self.target = bin_agent
        self.on_request = on_request
        bin_agent.served_by = self
        self.abort_reason = None
        self.state = CollectorState.TO_BIN

    def _move_towards(self, target):
        """Move TRUCK_SPEED cells along a Manhattan shortest path."""
        dx = target[0] - self.pos[0]  # cells still to go on each axis
        dy = target[1] - self.pos[1]
        step_x = (dx > 0) - (dx < 0)  # -1, 0 or +1
        step_y = (dy > 0) - (dy < 0)

        for _ in range(C.TRUCK_SPEED):
            if dx == 0 and dy == 0:
                break  # arrived
            if abs(dx) >= abs(dy):
                self.pos = (self.pos[0] + step_x, self.pos[1])
                dx -= step_x
            else:
                self.pos = (self.pos[0], self.pos[1] + step_y)
                dy -= step_y
            self.model.metrics.add_distance(1)

    # -- behaviour -------------------------------------------------------

    def step(self):
        if self.state is CollectorState.IDLE:
            # An idle truck that still carries waste and has no work left
            # goes back to the depot on its own.
            if self.load > 0 and not self.model.has_pending_work:
                self.state = CollectorState.TO_DEPOT
            return

        self.busy_steps += 1  # the truck was not idle at the start of this step

        # Safety rule: a bin that is too hot is skipped. If the truck was
        # still on its way it drops the job; if it already has a load it
        # goes straight back to the depot.
        if self.state is CollectorState.TO_BIN and self.target.alert_active:
            self._abort_job("target bin has a safety alert")
            return

        # The bin may also have been emptied by somebody else while we were
        # driving to it, or in the step we arrived. Driving on would mean a
        # trip that collects nothing, so the truck is released instead and
        # the dispatcher can give it something else. The fixed route ignores
        # this: it empties a bin on the schedule whatever the bin asked for.
        if self.state in (CollectorState.TO_BIN, CollectorState.LOADING) and self.on_request:
            if not self.target.has_request:
                self._abort_job("target bin no longer needs a collection")
                return

        if self.state is CollectorState.TO_BIN:
            self._move_towards(self.target.pos)
            if self.pos == self.target.pos:
                self.state = CollectorState.LOADING  # loading takes one step

        elif self.state is CollectorState.LOADING:
            self._take_waste()

        elif self.state is CollectorState.TO_DEPOT:
            self._move_towards(C.DEPOT_POS)
            if self.pos == C.DEPOT_POS:
                self.state = CollectorState.UNLOADING

        elif self.state is CollectorState.UNLOADING:
            self._finish_trip()  # unloading takes one step

    def _take_waste(self):
        """Empty as much of the target bin as the truck has room for.

        A bin that holds more than the truck can carry is only partly
        emptied: the truck takes what fits and leaves the rest.
        """
        free_space = self.capacity - self.load
        request_step = self.target.request_step  # remember it, collect() resets it

        taken = self.target.collect(min(free_space, self.target.level))
        self.load += taken
        self.trip_bins += 1

        # Waiting time is only counted once a request is actually closed.
        # A partial pickup leaves the request open, so the clock keeps running.
        waited = None
        if not self.target.has_request and request_step is not None:
            waited = self.model.now - request_step

        self.model.metrics.record_pickup(self, self.target, taken, waited)
        self.target.served_by = None
        self.target = None

        # Full truck: back to the depot. Otherwise park and wait for the
        # dispatcher, which may send us to another bin on the same trip.
        self.state = CollectorState.TO_DEPOT if self.load >= self.capacity else CollectorState.IDLE

    def _abort_job(self, reason):
        """Give up the current bin and let the dispatcher use us again.

        An empty truck goes straight back to being idle, a loaded one first
        drives back to the depot to unload.
        """
        self.target.served_by = None
        self.target = None
        self.abort_reason = reason
        self.state = CollectorState.TO_DEPOT if self.load > 0 else CollectorState.IDLE

    def _finish_trip(self):
        """Back at the depot: unload, empty the truck, close the trip."""
        self.model.metrics.record_trip(self.trip_bins)
        self.load = 0.0
        self.trip_bins = 0
        self.state = CollectorState.IDLE
        self.target = None

    def __str__(self):
        return f"{self.name} {self.state.value} @{self.pos} load {self.load:.0f}/{self.capacity:.0f}"


# ==========================================================================
# Dispatcher
# ==========================================================================


class DispatcherAgent(mesa.Agent):
    """The control room: decides which truck empties which bin.

    Every step it rebuilds the queue of bins that want to be emptied and are
    safe to touch, sorts them from the fullest to the emptiest, and sends
    the nearest free truck to each of them.
    """

    def __init__(self, model):
        super().__init__(model)
        self.queue = []  # bins waiting for a truck, most urgent first

    def step(self):
        self.refresh_queue()
        for bin_agent in list(self.queue):
            collector = self._nearest_free_collector(bin_agent)
            if collector is None:
                continue  # no truck left, the bin waits for the next step
            collector.assign(bin_agent)
            self.queue.remove(bin_agent)

    def refresh_queue(self):
        """Bins with an open request, no truck on the way and no safety alert."""
        self.queue = [
            bin_agent
            for bin_agent in self.model.bins
            if bin_agent.can_be_served and bin_agent.served_by is None
        ]
        # Fullest bin first. On a tie the lower bin id goes first, so two
        # bins with the same level never swap places between runs.
        self.queue.sort(key=lambda bin_agent: (-bin_agent.level, bin_agent.unique_id))

    def _nearest_free_collector(self, bin_agent):
        """The closest truck that is empty and has nothing to do.

        On a tie the lower numbered truck wins, which again keeps the
        decision the same on every run of the same scenario.
        """
        free = [c for c in self.model.collectors if c.is_free]
        if not free:
            return None
        return min(free, key=lambda c: (manhattan(c.pos, bin_agent.pos), c.index))

    def __str__(self):
        return f"Dispatcher with {len(self.queue)} bins in the queue"