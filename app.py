"""Live dashboard for the smart waste bin simulation.

    streamlit run app.py

The dashboard only ever *reads* the model and the scenario: it never changes
a parameter and never writes to the scenario. The only thing it does to a
model is step it forward, exactly like `demo.py` does.

Layout is one column so it works on a phone, and every table is kept to
three or four narrow columns so nothing needs sideways scrolling.
"""

import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.legend_handler import HandlerTuple
import pandas as pd
import streamlit as st

import config as C
from agents import BinStatus
from model import BaselineModel, SmartModel
from scenario import city_arrival_rate, generate_scenario, generate_scenarios

st.set_page_config(page_title="Smart waste bins", layout="centered")

# --------------------------------------------------------------------------
# the fixed status palette: colour means something, and never on its own
# --------------------------------------------------------------------------
BACKGROUND = "#FFFFFF"
GRID_LINE = "#E4E4E0"
TEXT = "#1A1A1A"
MUTED = "#6B6B6B"
TRUCK_COLOUR = "#33322E"

STATUS_COLOURS = {
    BinStatus.NORMAL: "#3A7D44",  # green
    BinStatus.NEARLY_FULL: "#D99A00",  # amber
    BinStatus.REQUESTING: "#E06C00",  # orange
    BinStatus.FULL: "#C62828",  # red
}
ALERT_COLOUR = "#7B1B1B"  # dark red, only ever with the alert marker

# A second, non-colour cue for every state, so the map still reads without
# colour: empty, a bar, a line, a cross, a cross plus the alert marker.
GLYPH = {
    BinStatus.NORMAL: None,
    BinStatus.NEARLY_FULL: "bar",
    BinStatus.REQUESTING: "line",
    BinStatus.FULL: "cross",
}

LEVEL_LABELS = {
    "low": "low - 40%",
    "medium": "medium - 70%",
    "high": "high - 100%",
    "overload": "overload - 130%",
}

VIEW_LABELS = {"Compare": "compare", "Smart": "smart", "Baseline": "baseline"}


# --------------------------------------------------------------------------
# scenario and models
# --------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def scenario_for(seed):
    """The generated day for a seed. Cached, and copied on every call."""
    return generate_scenario(seed)


@st.cache_data(show_spinner=False)
def calibration_arrival():
    """The same city arrival rate the experiment used.

    Taking it from the whole experiment set (not from the one displayed
    scenario) is what makes the dashboard reproduce results.csv exactly.
    """
    return city_arrival_rate(generate_scenarios(C.N_SCENARIOS, C.BASE_SEED))


def build_models(seed, level):
    """A fresh pair of models on one and the same scenario."""
    scenario = scenario_for(seed)
    load_scale = C.load_scale_for(C.level_share(level), calibration_arrival())
    models = {
        "baseline": BaselineModel(scenario, load_scale=load_scale),
        "smart": SmartModel(scenario, load_scale=load_scale),
    }
    return scenario, models


def reset_state(seed, level):
    st.session_state.scenario, st.session_state.models = build_models(seed, level)
    st.session_state.playing = False


def advance(steps=1):
    for _ in range(steps):
        for model in st.session_state.models.values():
            model.step()


def play_delay(speed):
    """Seconds between two steps, from the speed slider (1 slow, 10 fast)."""
    return max(0.05, 0.6 - 0.055 * speed)


# --------------------------------------------------------------------------
# the map
# --------------------------------------------------------------------------


def draw_marker(ax, centre, kind, colour):
    """Draw the shape that goes on top of a coloured cell."""
    x, y = centre
    if kind == "bar":
        ax.plot([x - 0.18, x + 0.18], [y, y], color=BACKGROUND, linewidth=1.8,
                solid_capstyle="round", zorder=4)
    elif kind == "line":
        ax.plot([x, x], [y - 0.19, y + 0.13], color=BACKGROUND, linewidth=1.8,
                solid_capstyle="round", zorder=4)
    elif kind == "cross":
        for sign in (1, -1):
            ax.plot([x - 0.16 * sign, x + 0.16 * sign], [y - 0.16, y + 0.16],
                    color=BACKGROUND, linewidth=1.8, solid_capstyle="round", zorder=4)
    if kind == "alert":
        ax.plot([x + 0.26], [y + 0.26], marker="^", markersize=5.5, color=BACKGROUND,
                markeredgecolor=TEXT, markeredgewidth=0.5, zorder=6)


def draw_map(model, size=3.6):
    """The city grid: bins by status, trucks, depot, and a legend."""
    figure, axes = plt.subplots(figsize=(size, size + 0.6))
    figure.patch.set_facecolor(BACKGROUND)
    axes.set_facecolor(BACKGROUND)
    axes.set_xlim(0, C.GRID_WIDTH)
    axes.set_ylim(-2.6, C.GRID_HEIGHT)  # room for the legend underneath
    axes.set_xticks([])
    axes.set_yticks([])
    axes.set_aspect("equal")
    for side in axes.spines.values():
        side.set_visible(False)

    for x in range(C.GRID_WIDTH + 1):
        axes.plot([x, x], [0, C.GRID_HEIGHT], color=GRID_LINE, linewidth=0.5, zorder=0)
    for y in range(C.GRID_HEIGHT + 1):
        axes.plot([0, C.GRID_WIDTH], [y, y], color=GRID_LINE, linewidth=0.5, zorder=0)

    for bin_agent in model.bins:
        centre = (bin_agent.pos[0] + 0.5, bin_agent.pos[1] + 0.5)
        hot = bin_agent.alert_active
        colour = ALERT_COLOUR if hot else STATUS_COLOURS[bin_agent.status]
        axes.add_patch(plt.Rectangle((centre[0] - 0.36, centre[1] - 0.36), 0.72, 0.72,
                                     facecolor=colour, edgecolor="none", zorder=2))
        draw_marker(axes, centre, "alert" if hot else GLYPH[bin_agent.status], colour)

    depot = (C.DEPOT_POS[0] + 0.5, C.DEPOT_POS[1] + 0.5)
    axes.add_patch(plt.Rectangle((depot[0] - 0.4, depot[1] - 0.4), 0.8, 0.8,
                                 facecolor="none", edgecolor=TRUCK_COLOUR, linewidth=2,
                                 zorder=3))

    for collector in model.collectors:
        centre = (collector.pos[0] + 0.5, collector.pos[1] + 0.5)
        axes.add_patch(plt.Circle(centre, 0.33, facecolor=TRUCK_COLOUR, edgecolor=BACKGROUND,
                                  linewidth=1, zorder=5))
        axes.text(centre[0], centre[1], str(collector.index + 1), color=BACKGROUND,
                  ha="center", va="center", fontsize=6.5, zorder=6)

    _draw_legend(axes)
    figure.tight_layout(pad=0.2)
    return figure


def _draw_legend(axes):
    """A legend under the grid, using the same shapes as the map itself.

    matplotlib lays the entries out, so the labels never overlap, and each
    coloured square carries the same marker as the cell it stands for.
    """
    marker_for = {None: None, "bar": "_", "line": "|", "cross": "x", "alert": "^"}

    def cell(colour, marker):
        """A coloured square with the same marker the cell carries."""
        return (plt.Rectangle((0, 0), 1, 1, facecolor=colour, edgecolor="none"),
                Line2D([0], [0], marker=marker_for[marker], color=BACKGROUND,
                       markerfacecolor=BACKGROUND, markersize=6, linestyle="none"))

    # matplotlib fills a legend column by column, so the entries are handed
    # over in an interleaved order to make the two rows read left to right.
    handles = [cell(STATUS_COLOURS[BinStatus.NORMAL], None),
               cell(ALERT_COLOUR, "alert"),
               cell(STATUS_COLOURS[BinStatus.NEARLY_FULL], "bar"),
               plt.Circle((0, 0), 0.5, facecolor=TRUCK_COLOUR),
               cell(STATUS_COLOURS[BinStatus.REQUESTING], "line"),
               plt.Rectangle((0, 0), 1, 1, facecolor="none", edgecolor=TRUCK_COLOUR,
                             linewidth=1.5),
               cell(STATUS_COLOURS[BinStatus.FULL], "cross")]
    labels = ["normal", "alert", "nearly full", "truck", "request", "depot", "full"]

    axes.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.0),
                ncol=4, fontsize=7, frameon=False, handlelength=1.1, handleheight=1.1,
                handletextpad=0.35, columnspacing=0.9, labelspacing=0.35,
                borderpad=0.0,
                handler_map={tuple: HandlerTuple(ndivide=None, pad=0.1)})


# --------------------------------------------------------------------------
# the tables
# --------------------------------------------------------------------------


def fill_table(model):
    """Every bin, fullest first: bin, level, state."""
    rows = sorted(model.bins, key=lambda b: -b.level)
    return pd.DataFrame(
        {
            "bin": [b.unique_id for b in rows],
            "level": [f"{b.level:.0f} u" for b in rows],
            "state": [b.status.value if not b.alert_active else "alert" for b in rows],
        }
    )


def queue_table(model):
    """Bins waiting for a truck: bin, level, how long they have waited."""
    waiting = sorted((b for b in model.bins if b.has_request),
                     key=lambda b: -(model.now - b.request_step))
    return pd.DataFrame(
        {
            "bin": [b.unique_id for b in waiting],
            "level": [f"{b.level:.0f} u" for b in waiting],
            "waiting": [f"{model.now - b.request_step} steps" for b in waiting],
        }
    )


def history_table(model, last=12):
    """The most recent pickups: step, bin, units, wait."""
    rows = model.metrics.collection_log[-last:][::-1]
    return pd.DataFrame(
        {
            "step": [row["step"] for row in rows],
            "bin": [row["bin"] for row in rows],
            "units": [f"{row['units']:.0f} u" for row in rows],
            "wait": ["-" if row["waited"] is None else f"{row['waited']}" for row in rows],
        }
    )


def metric_rows(model):
    """The five headline metrics, compact, with their unit."""
    done = model.metrics.summary()
    return pd.DataFrame(
        {
            "metric": ["Overflow (episodes)", "Uncontained (units)", "Distance (cells)",
                       "Avg wait (steps)", "Unserved (bins)"],
            "value": [f"{done['overflow_episodes']:,}",
                      f"{done['uncontained_waste']:,.0f}",
                      f"{done['total_distance']:,}",
                      "n/a" if done["avg_waiting_completed"] is None
                      else f"{done['avg_waiting_completed']:.1f}",
                      f"{done['unserved_requests']:,}"],
        }
    )


def show_model(model, title, view, size=3.6):
    """One strategy: map, metrics, fill levels, queue, history.

    `view` and `title` are part of every table key, so the tables of the two
    strategies never share an element when Compare shows both of them.
    """
    st.markdown(f"**{title}**")
    st.pyplot(draw_map(model, size), width="stretch")

    st.markdown("##### Metrics")
    st.dataframe(metric_rows(model), hide_index=True, width="stretch",
                 column_order=["metric", "value"], key=f"metrics_{view}_{title}")

    st.markdown("##### Fill levels")
    st.dataframe(fill_table(model), hide_index=True, width="stretch", height=230,
                 column_order=["bin", "level", "state"], key=f"fill_{view}_{title}")

    st.markdown("##### Request queue")
    queue = queue_table(model)
    if queue.empty:
        st.caption("Empty - no bin is waiting for a truck.")
    else:
        st.dataframe(queue, hide_index=True, width="stretch", height=160,
                     column_order=["bin", "level", "waiting"], key=f"queue_{view}_{title}")

    st.markdown("##### Collection history")
    history = history_table(model)
    if history.empty:
        st.caption("No pickup yet.")
    else:
        st.dataframe(history, hide_index=True, width="stretch", height=180,
                     column_order=["step", "bin", "units", "wait"],
                     key=f"history_{view}_{title}")


# --------------------------------------------------------------------------
# results tab
# --------------------------------------------------------------------------

CHART_METRICS = [
    ("overflow_episodes", "overflow episodes"),
    ("uncontained_waste", "uncontained waste"),
    ("total_distance", "total distance"),
    ("avg_waiting_completed", "avg wait (served)"),
    ("unserved_requests", "unserved requests"),
    ("avg_bins_per_trip", "bins per trip"),
    ("truck_utilisation", "truck utilisation"),
    ("mean_queue_length", "mean queue length"),
    ("max_waiting_completed", "max wait (served)"),
    ("max_open_wait", "max wait (still open)"),
    ("full_bins_at_end", "bins at 100% at end"),
    ("units_collected", "units collected"),
]


def show_results_tab():
    st.markdown("##### Charts")
    st.caption("Saved by `python run_experiment.py`. Load levels are fractions of the "
               "never-idle capacity estimate; the 130% level was relabelled in stage 4c.")
    choice = st.selectbox(
        "Chart",
        [label for _, label in CHART_METRICS],
        label_visibility="collapsed",
    )
    column = next(c for c, label in CHART_METRICS if label == choice)
    try:
        st.image(f"chart_{column}.png", width="stretch")
    except FileNotFoundError:
        st.warning("Chart file not found. Run `python run_experiment.py` first.")

    with st.expander("Duration sweep - added after seeing the 4b results"):
        st.caption("The four original load levels at 1x, 3x and 6x the length of a day, "
                   "20 scenarios each, both strategies.")
        try:
            sweep = pd.read_csv("results_duration.csv")
        except FileNotFoundError:
            st.warning("results_duration.csv not found. Run `python run_experiment.py` first.")
            return

        level = st.selectbox("Load level", list(C.LOAD_LEVELS),
                             format_func=lambda name: LEVEL_LABELS[name])
        duration = st.selectbox("Day length", ["1x", "3x", "6x"],
                                format_func=lambda d: f"{d} ({C.HORIZON * int(d[0])} steps)")
        cell = sweep[(sweep["load"] == level) & (sweep["duration"] == duration)]
        table = pd.DataFrame({
            "metric": ["Overflow (episodes)", "Uncontained (units)", "Unserved (bins)",
                       "Queue length", "Net fill (units)"],
        })
        for strategy in ("baseline", "smart"):
            rows = cell[cell["strategy"] == strategy]
            table[strategy] = [f"{rows['overflow_episodes'].mean():,.2f}",
                               f"{rows['uncontained_waste'].mean():,.1f}",
                               f"{rows['unserved_requests'].mean():,.2f}",
                               f"{rows['mean_queue_length'].mean():,.2f}",
                               f"{rows['net_bin_fill_change'].mean():+,.1f}"]
        st.dataframe(table, hide_index=True, width="stretch",
                     column_order=["metric", "baseline", "smart"],
                     key=f"duration_{level}_{duration}")

    with st.expander("Transient vs steady state"):
        st.write(
            "The bins open the day already holding about 1,350 units, so a one-day run "
            "measures a transient. The dispatcher's perfect record at 130% - zero overflow "
            "- is gone by the third day, where all 20 scenarios spill. Use one day for "
            "'what does a normal day look like', and the duration sweep for 'which strategy "
            "keeps up when the backlog is real'."
        )


# --------------------------------------------------------------------------
# the app
# --------------------------------------------------------------------------

st.markdown("### Smart waste bins")
st.caption("Mesa 3 - 20x20 grid, 30 bins, 3 trucks, 1 depot")

tab_simulation, tab_results = st.tabs(["Simulation", "Results"])

with tab_simulation:
    view = st.segmented_control(
        "View", list(VIEW_LABELS), default="Compare",
        help="Both strategies always run on the same scenario; this only changes what is shown.",
    )
    if view is None:
        view = "Compare"

    left, right = st.columns([1, 2])
    with left:
        level = st.selectbox("Load level", list(C.LOAD_LEVELS),
                             index=list(C.LOAD_LEVELS).index("medium"),
                             format_func=lambda name: LEVEL_LABELS[name])
    with right:
        seed = int(st.number_input("Seed", value=C.BASE_SEED, step=1))

    speed = st.slider("Speed", 1, 10, 5)

    if ("models" not in st.session_state
            or st.session_state.get("config") != (seed, level)):
        reset_state(seed, level)
        st.session_state.config = (seed, level)

    button_row = st.columns(3)
    with button_row[0]:
        if st.button("Step", width="stretch"):
            st.session_state.playing = False
            advance()
    with button_row[1]:
        if st.button("Pause" if st.session_state.playing else "Play", width="stretch"):
            if not st.session_state.models["smart"].is_done:
                st.session_state.playing = not st.session_state.playing
    with button_row[2]:
        if st.button("Reset", width="stretch"):
            reset_state(seed, level)

    models = st.session_state.models
    horizon = models["smart"].horizon
    done = min(models["smart"].now, horizon)

    st.progress(done / horizon)
    st.caption(f"Step {done} of {horizon} - load {level} - seed {seed}")
    if done >= horizon:
        st.caption("Day finished. Reset to run it again.")

    if VIEW_LABELS[view] == "compare":
        for title, key in (("Smart", "smart"), ("Baseline", "baseline")):
            show_model(models[key], title, view)
    else:
        strategy = VIEW_LABELS[view]
        show_model(models[strategy], strategy.capitalize(), view)

    if st.session_state.playing and not models["smart"].is_done:
        time.sleep(play_delay(speed))
        advance()
        st.rerun()

with tab_results:
    show_results_tab()