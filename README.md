# Smart waste bin simulation

A multi-agent simulation of a city with smart waste bins, built with
[Mesa 3.x](https://mesa.readthedocs.io/). Two collection strategies are
compared on exactly the same pre-generated scenarios:

* **baseline** - the trucks follow a fixed round through the city,
* **smart** - a dispatcher sends the nearest free truck to whichever bin
  needs emptying most.

There is no machine learning anywhere; every agent follows written rules.

## Run commands

Use the virtual environment in this folder:

```bash
source .venv/bin/activate
```

| Command | What it does |
| --- | --- |
| `python demo.py 1` | The rules of one bin, and one bin being filled, emptied and reset |
| `python demo.py 2` | The dispatcher serves the fullest bin first, partial pickup, the same bin reassigned |
| `python demo.py 3` | Three trucks in parallel, no double assignment, cancelled jobs, tie-breaking |
| `python demo.py 4` | One scenario replayed under both strategies |
| `python demo.py all` | All checks |
| `python run_experiment.py` | 20 scenarios x 7 load levels x 3 strategies -> tables, `results.csv`, 16 charts, plus a 1x/3x/6x duration sweep in `results_duration.csv` |
| `python test_dashboard.py` | Checks the dashboard reproduces `results.csv` exactly for one seed |
| `streamlit run app.py` | The dashboard, in a browser (`localhost:8501`) |

`run_experiment.py` writes `results.csv` and `chart_<metric>.png` (one per
metric) into the folder it is run from.

## Deploy

The dashboard runs on Streamlit Community Cloud as it is - there is nothing
to configure and no secret to set.

**1. Put the folder in a GitHub repository.** Only these files are needed
for the dashboard; `.gitignore` keeps `.venv/` and `__pycache__/` out:

```
app.py                 the dashboard
agents.py  model.py  scenario.py  config.py
.streamlit/config.toml  the theme (committed on purpose)
requirements.txt
results_duration.csv   read by the Results tab
chart_*.png            read by the Results tab
README.md
```

`results.csv` is committed too, although only the experiment reads it.

**2. Create the app.** Go to [share.streamlit.io](https://share.streamlit.io),
choose *Deploy*, and pick the repository and branch. Set

* **Main file path** - `app.py`
* **Python version** - 3.13

Python 3.13 is the safe choice: mesa requires **3.12 or newer** and this
project was developed on 3.14.4. Anything below 3.12 will not install mesa.

**3. Deploy.** Cloud builds from `requirements.txt`, which pins the five
packages the code actually needs:

```
mesa==3.5.1  streamlit==1.65.0  pandas==3.0.6  matplotlib==3.11.2  networkx==3.7
```

`networkx` looks out of place - this project never imports it. It is listed
because mesa 3.5.1 ships `import networkx` inside `mesa.discrete_space.network`
without declaring the dependency, so without it the app dies on startup with
`ModuleNotFoundError: No module named 'networkx'`.

**What happens when the app starts.** The first page takes about 15 seconds,
almost all of it `import mesa` (6 s) and `import streamlit` (2 s). The
20-scenario experiment is **not** run on load - it takes minutes. The app
only generates the 20 calibration scenarios (0.3 s, cached) so that the load
scale is exactly the one the experiment used, and then builds two empty
models for the selected seed.

**If the data files are missing.** The Results tab catches it and shows a
warning instead of crashing. To regenerate `results.csv`,
`results_duration.csv` and the charts, run `python run_experiment.py` locally
and commit the output again.

**Not deployed.** `demo.py`, `test_dashboard.py` and `run_experiment.py` are
development tools; they are harmless on the server but nothing links to them.

## Files

| File | Contents |
| --- | --- |
| `config.py` | Every tunable number, plus the load calibration |
| `scenario.py` | Pre-generated, replayable scenarios built from a seed |
| `agents.py` | `BinAgent`, `CollectorAgent`, `DispatcherAgent` |
| `model.py` | `CityModel` and the three strategies, plus the metrics |
| `demo.py` | The staged behaviour checks |
| `run_experiment.py` | The experiment, the tables and the charts |
| `test_dashboard.py` | Proves the dashboard is the same simulation as the experiment |
| `app.py` | The Streamlit dashboard |
| `.streamlit/config.toml` | The theme: one background, one accent, no styling in `app.py` |

## The dashboard

`streamlit run app.py` opens a single-column page that works on a phone:

* **Simulation tab** - a view selector (Smart / Baseline / Compare), the four
  original load levels, a seed, a speed slider, and Step / Play / Reset.
  Both strategies always run on the *same* scenario in lockstep; the view
  selector only changes what is drawn. Below the map: the five headline
  metrics, every bin with its fill level and state, the request queue with
  how long each bin has been waiting, and the last pickups.
* **Results tab** - the charts saved by `run_experiment.py`, the duration
  sweep from `results_duration.csv` with its "added after seeing the 4b
  results" labels, and the transient vs steady state note.

The bins are coloured by status *and* carry a second, non-colour cue, so the
map still reads without colour: empty square = normal, a horizontal bar =
nearly full, a vertical bar = asking for a collection, a cross = full, and a
white triangle = safety alert. Trucks are numbered circles and the depot is
an outlined square.

The dashboard only ever reads the model and the scenario. It never changes
a parameter and never writes to the scenario, which
`python test_dashboard.py` checks by stepping a full day through the same
code path the buttons use and comparing all 16 metrics per strategy with
the row that `run_experiment.py` saved for that seed.

## How one step works

No schedulers are used. `CityModel.step()` walks through `model.agents`
itself, in a fixed order:

1. the scenario drops waste into the bins,
2. the scenario temperatures are applied (safety alerts),
3. the strategy decides who drives where,
4. the trucks move and load,
5. the step counter goes up.

A scenario is only ever read, never written to. `demo.py 4` checks this by
comparing the scenario object with a freshly generated one from the same
seed after both strategies have run.

## The two strategies

### Baseline: fixed routes

The 30 bins are cut into one route per truck by a deterministic rule
(`model.build_routes`), with no random numbers involved:

1. **Sector.** The compass around the depot is cut into three equal
   wedges. A bin belongs to the wedge its direction from the depot points
   at.
2. **Order inside a wedge.** The bins of a wedge are visited in
   nearest-neighbour order: starting at the depot, always drive to the
   closest bin of that wedge that is not on the list yet.

A free truck always takes the next bin on its list and empties whatever it
finds there, including from bins that never asked for a collection
(`on_request=False`). When the truck is full it drives back to the depot,
unloads and carries on with the rest of its route. The baseline never looks
at a collection request. The safety rule is identical to the smart
strategy: a bin with a temperature alert is skipped and comes back on the
next round of the route.

The wedges do not end up the same size - with 30 randomly placed bins one
truck can easily get twice as many bins as another. That imbalance is part
of the baseline and is not corrected.

### Smart: dispatcher

Every step the dispatcher rebuilds the queue of bins that want to be
emptied and are safe to touch, sorts it from the fullest bin down, and
sends the **nearest free truck** to each of them in turn. Ties are always
broken the same way, so the same scenario always produces the same run:
equal fill levels go to the lower bin id, equal distances to the lower
truck id.

A truck that is sent to a bin gives the job up if the bin raises a safety
alert, or if the bin is emptied by somebody else before the truck arrives.
A released truck becomes free again and is given other work on the next
step, instead of driving on for a pickup that would collect nothing.

## How busy the city is: load calibration

The scenarios are generated once with a nominal amount of waste. How busy
the city really is decided by a `LOAD_SCALE` multiplier that the model
applies while it reads the arrivals - the scenario itself is never touched,
so both strategies always face the same waste at the same step.

**Fleet throughput.** How much a fleet that never stands still can collect
per step:

```
typical bin distance d  = average grid distance from the depot to a cell  = 10.0 cells
round trip steps        = 2 * d / TRUCK_SPEED + 2                       = 12 steps
never-idle capacity     = N_COLLECTORS * TRUCK_CAPACITY / round trip    = 3 * 20 / 12
                                                                            = 5.0 units/step
```

The two extra steps are loading a bin and unloading at the depot. This is a
*never-idle* estimate: it says what the fleet could collect if every truck
spent every step on a full 20 unit pickup. The dispatcher deliberately does
not do that - at the 130 % level it is only busy 0.51 ± 0.05 of the day,
because it waits until a bin is worth the trip. That is why every load
level below is named as a fraction of the **never-idle capacity estimate**
rather than as "of the fleet".

The city arrival rate is *measured* from the generated scenarios rather than
assumed, because every arrival is clipped at zero, which pushes the average
above the nominal rate.

**Load levels** are fractions of that never-idle estimate:

| level | fraction | waste per step | load scale | |
| --- | --- | --- | --- | --- |
| low | 40 % | 2.0 units/step | 0.135 | original sweep |
| medium | 70 % | 3.5 units/step | 0.236 | original sweep |
| high | 100 % | 5.0 units/step | 0.337 | original sweep |
| overload | **130 % of the never-idle capacity estimate** (measured smart utilisation 0.51 ± 0.05) | 6.5 units/step | 0.438 | original sweep |
| ext_200 | 200 % | 10.0 units/step | 0.674 | **added after seeing the first sweep** |
| ext_300 | 300 % | 15.0 units/step | 1.010 | **added after seeing the first sweep** |
| ext_500 | 500 % | 25.0 units/step | 1.684 | **added after seeing the first sweep** |

**Choosing the reference load.** The rule lives in
`run_experiment.py::calibrate_reference_load` (in the docstring of that
function) and is repeated here:

> A run counts as *saturated* when more than 20 % of the bins are still
> unserved at the end of the day. The **reference load** is the highest
> load level whose average over all scenarios stays at or below that share.

`run_experiment.py` applies this to the **baseline only** and prints the
result. The three `ext_` levels were added afterwards, when it turned out
that the four original levels never made the baseline saturate. They are
only used to find out where each strategy breaks
(`print_breaking_points`). They are **not** part of the calibration: the
reference load is still chosen from the original four, and it is still
`overload`. No parameter is tuned anywhere to make one strategy look
better than the other.

### When the load scale was corrected, and what that means

The load scale was corrected **after** a strategy comparison had already
been printed, and that should be said plainly.

* The first version multiplied the arrivals by
  `level * FLEET_THROUGHPUT / NOMINAL_CITY_ARRIVAL`, with
  `NOMINAL_CITY_ARRIVAL = N_BINS * 0.3 = 9.0` units/step taken from the
  generator settings. That gave load scales 0.222 / 0.389 / 0.556 / 0.722.
* With those scales the full experiment was run and **printed**: all four
  load level tables, the charts and the "what the numbers say" section.
* Only afterwards, while writing `demo.py 4`, did it become visible that
  the generated scenarios actually produce **14.85** units per step, not
  9.0. Every arrival is clipped at zero, which pushes the average up by
  about 65 %, so the "100 %" level was really about 168 % load.
* The fix was to *measure* the city arrival rate instead of assuming it
  (`scenario.city_arrival_rate`), which gave the scales in the table
  above, 0.135 to 0.438 for the original four levels.

So: the first printed comparison used load levels that were mislabelled by
a factor of about 1.6, and the correction happened after it had been read.
Nothing about the comparison itself was adjusted afterwards; the rule, the
parameters and the strategies stayed as they were. The one visible
consequence is that the reference load moved from `medium` to `overload`,
because the corrected sweep is a harder sweep and the baseline keeps up
further on it. The rule was never changed to produce that outcome.

### Does the throughput formula hold up?

`run_experiment.py` prints the check (`print_throughput_check`). The
never-idle estimate predicts 5.00 units/step. Measured at the reference
load:

| measured | value | vs the formula |
| --- | --- | --- |
| baseline, whole day | 4.85 units/step | 97 % - the estimate holds here |
| smart, whole day | 2.54 units/step | 51 % - estimate is 49 % too high |
| smart, best 12-step burst | 7.25 units/step | 145 % - a burst, not a rate |
| busiest single step | 36 units | not comparable |

The estimate is a capacity figure for a fleet that never idles, and the
baseline is the strategy that never idles (100 % utilisation), so it
describes the baseline almost exactly. The dispatcher deliberately waits
until a bin crosses 90 units (51 % utilisation), so it never approaches
that capacity. The estimate must not be used as a prediction of what the
smart strategy collects.

## Transient vs steady state

*(added after seeing the 4b results)*

Everything above is measured over **one day of 300 steps**, and that choice
matters more than it looks.

**What one day does not show.** The bins do not start empty. They start with
10 to 80 units each, so the city opens the day holding roughly 1,350 units
of waste. That inventory absorbs new arrivals before anything can spill. A
strategy therefore looks good on day one partly because it is spending an
opening balance, and the longer the day runs the more of that balance is
gone.

The mass balance (`print_mass_balance`) shows this directly. At the
reference load the dispatcher finishes the day **+1,189 units above where it
started** and the baseline **+456**. Neither is steady: the dispatcher is
building a backlog and the baseline is building a smaller one. A third of
what the city offered at 40 % load was never collected by anyone, yet
nobody spills, because the inventory absorbs it.

**What happens over a longer run.** The duration sweep
(`run_duration_sweep`, `results_duration.csv`) repeats the four original
load levels at 1x, 3x and 6x the length of a day, 20 scenarios each. The
one-day result that looked like the headline of this project does not
survive:

| 130 % of never-idle | overflow episodes | uncontained waste | scenarios that spill |
| --- | --- | --- | --- |
| 1x (300 steps) | 0.00 ± 0.00 | 0.00 | 0 of 20 |
| 3x (900 steps) | 118.10 ± 15.18 | 702.54 ± 246.20 | 20 of 20 |
| 6x (1800 steps) | 335.75 ± 16.58 | 2,170.79 ± 601.78 | 20 of 20 |

**The dispatcher's perfect record at 130 % is a transient.** It holds for
one day and is gone by the third, where every single scenario spills. The
same thing happens one level lower: at 100 % the dispatcher is still clean
after 900 steps (0 overflow episodes against the baseline's 19.25) but
spills by 1800 steps (90.00 against 80.45).

**What one day does show.** It shows the transient honestly: which
strategy reacts first, how quickly a request is answered once a truck is
free, and how much driving a day costs. Those numbers are stable - the
1x cells of the sweep reproduce the main experiment exactly, which the
script asserts.

**How to read the two together.** One day answers "what does a normal day
look like". The longer runs answer "which strategy keeps up when the
backlog is real". Both are needed, and quoting only the first is what
would make the smart strategy look better than it is.

## Metrics

| Metric | Meaning |
| --- | --- |
| overflow episodes | Counted once, on the step where a bin first reaches 100 %, and again only after a collection has emptied it |
| uncontained waste | Total waste that no longer fitted into a full bin |
| total distance | Cells driven by all trucks |
| avg wait (served) | Steps from crossing the collection threshold to the pickup that closes the request, averaged over the requests that were actually served |
| unserved requests | Requests still open at the end of the day |
| bins per trip | Bins emptied per finished trip |
| max wait (served) | Longest wait of any single request that was answered |
| max wait (still open) | Longest wait of a request that is still open at the end |
| bins at 100 % at end | How many bins are completely full when the day ends |
| truck utilisation | Share of the day each truck was not idle, averaged over the trucks. A step counts as busy when the truck is not idle at the start of it |
| mean queue length | Average number of bins waiting for a truck, measured per step for every strategy |
| units collected | Total waste taken out of the bins |
| units collected/step | That total divided by the length of the day |
| best sustained/step | Highest average pickup rate over any stretch of one round trip |
| busiest single step | Most units picked up in one single step |
| waste offered | Total waste that dropped into the city, load scale included |
| net bin fill change | How much more (or less) waste the bins hold at the end than at the start |

Two more numbers are recorded for the report: `requests_raised` (used for
the invariant `requests_raised == completed_pickups + unserved_requests`,
checked on every run of both strategies) and `unserved_mean_distance` (how
far from the depot the bins that are still waiting sit).

Every run asserts two identities:

```
requests_raised == completed_pickups + unserved_requests
offered == collected + uncontained + (bin contents at the end - at the start)
```

The second one is the mass balance: nothing may be lost and nothing may
appear. The tolerance is 0.25 units, which covers the two decimals the bin
levels are stored with; the largest gap seen across all 420 runs is 0.05.

A run in which a truck never finished a trip reports no value for
`avg_bins_per_trip` rather than zero, because the average is taken over
finished trips only.

## Limitations

* **The baseline is deliberately naive.** It walks a fixed route and
  empties whatever it finds, including from bins that never asked for a
  collection. That is a fair comparison for "no dispatcher at all", but it
  is not the strongest possible fixed schedule.
* **A tuned-frequency baseline would probably narrow the gap.** If the
  fixed route visited the busy bins more often and the quiet corners less
  often - for example by ordering the route by how fast a bin fills - it
  would waste fewer trips without any dispatcher at all. The gap measured
  here is between *no dispatching at all* and *a dispatcher*, not between
  *a bad schedule* and *a good schedule*.
* **One metric flatters the baseline.** "Unserved requests" only counts
  bins that are still above the collection threshold. The baseline empties
  bins that never asked, which pushes them below the threshold, so it looks
  better on that number while causing more uncontained waste. Overflow and
  uncontained waste are the fairer pair.
* **The sector split is uneven.** Cutting the grid into three wedges gives
  one truck 13 bins and another 6 in the example scenario. That imbalance
  is part of the baseline as specified and is not corrected.
* **One bin fills one truck.** With a 20 unit truck and a 90 unit
  threshold, almost every trip covers exactly one bin, which is why
  "bins per trip" sits near 1 for both strategies.
* **The city is one square grid with one central depot** and trucks drive
  Manhattan paths. Obstacles, several depots, or a different grid would
  change the distance totals.
* **The load levels are a scenario-wide multiplier**, so every bin gets
  busier at the same time. Real peaks hit some streets and not others.