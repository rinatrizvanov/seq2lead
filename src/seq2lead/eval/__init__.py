"""M8: baselines, evaluation and the leaderboard."""

from seq2lead.eval.cohort import Cohort, build_cohort
from seq2lead.eval.config import ExperimentConfig, load_experiment

EVAL_VERSION = "m8/v1"

__all__ = ["EVAL_VERSION", "Cohort", "ExperimentConfig", "build_cohort", "load_experiment"]
