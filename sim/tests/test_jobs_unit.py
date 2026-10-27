import math
import random

from sim import config as c
from sim.jobs import TrainingJob, arrival_rate_per_hour, generate, rng_for

N_SLOTS = c.WINDOW_HOURS * c.SLOTS_PER_HOUR


def test_rng_for_is_deterministic_and_purpose_specific():
    a = rng_for(c.SEED, "peak", "jobs")
    b = rng_for(c.SEED, "peak", "jobs")
    assert isinstance(a, random.Random)
    assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]
    x = rng_for(c.SEED, "peak", "idle").random()
    y = rng_for(c.SEED, "peak", "jobs").random()
    assert x != y


def test_same_seed_and_scenario_is_identical():
    assert generate(N_SLOTS, c.SEED, "peak") == generate(N_SLOTS, c.SEED, "peak")


def test_different_scenarios_or_seeds_differ():
    peak = generate(N_SLOTS, c.SEED, "peak")
    assert peak != generate(N_SLOTS, c.SEED, "sun")
    assert peak != generate(N_SLOTS, c.SEED + 1, "peak")


def test_bounds_steps_and_slot_floor():
    jobs = generate(N_SLOTS, c.SEED, "peak")
    assert jobs
    for j in jobs:
        lo, hi = c.JOB_POWER_KW
        assert lo <= j.power_kw <= hi and (j.power_kw - lo) % c.JOB_POWER_STEP_KW == 0
        lo, hi = c.JOB_DURATION_MIN
        assert lo <= j.duration_min <= hi and (j.duration_min - lo) % c.JOB_DURATION_STEP_MIN == 0
        lo, hi = c.JOB_SLACK_MIN
        assert lo <= j.slack_min <= hi and (j.slack_min - lo) % c.JOB_SLACK_STEP_MIN == 0
        assert isinstance(j.arrival_slot, int) and 0 <= j.arrival_slot < N_SLOTS
        assert j.arrival_min % c.SLOT_MINUTES == 0


def test_arrivals_sorted_and_ids_count_up():
    jobs = generate(N_SLOTS, c.SEED, "peak")
    assert [j.id for j in jobs] == [str(c.JOB_ID_BASE + i) for i in range(len(jobs))]
    slots = [j.arrival_slot for j in jobs]
    assert slots == sorted(slots)


def test_deadline_min():
    j = TrainingJob(id="4001", arrival_slot=4, power_kw=100, duration_min=60, slack_min=360)
    assert j.deadline_min == 4 * c.SLOT_MINUTES + 60 + 360


def test_zero_slots_gives_no_jobs():
    assert generate(0, c.SEED, "peak") == []


def test_mean_job_count_matches_rate():
    n_slots = 400 * 24 * c.SLOTS_PER_HOUR
    hours = n_slots / c.SLOTS_PER_HOUR
    expected = arrival_rate_per_hour() * hours
    count = len(generate(n_slots, c.SEED, "peak"))
    assert math.isclose(count, expected, rel_tol=0.03)


def test_mean_power_and_duration_give_training_load():
    jobs = generate(400 * 24 * c.SLOTS_PER_HOUR, c.SEED, "peak")
    mean_power = sum(j.power_kw for j in jobs) / len(jobs)
    mean_duration = sum(j.duration_min for j in jobs) / len(jobs)
    assert math.isclose(mean_power, sum(c.JOB_POWER_KW) / 2, rel_tol=0.03)
    assert math.isclose(mean_duration, sum(c.JOB_DURATION_MIN) / 2, rel_tol=0.03)
