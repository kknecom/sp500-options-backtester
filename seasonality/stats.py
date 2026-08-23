"""
Statistical significance testing for calendar effects, following Chapter 4
of the BetterTrader "Backtesting 101" guide: don't just eyeball an average
return difference -- run a t-test against the null hypothesis that the
in-window mean return is zero (or, better, that it equals the out-of-window
mean), and report a Sharpe-style stat alongside it.

Also implements a Bonferroni correction: testing ~20 calendar windows at
once (as run_seasonality.py does) means a naive 0.05 significance cutoff
will produce roughly one false positive by chance alone. Divide alpha by
the number of effects tested before calling anything "significant."
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from statistics import mean, pstdev

try:
    from scipy import stats as scipy_stats
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False


@dataclass
class EffectResult:
    name: str
    n_in: int
    n_out: int
    mean_in: float
    mean_out: float
    stdev_in: float
    t_stat: float
    p_value: float
    annualized_sharpe_in: float


def evaluate_effect(name: str, in_window: list[float], out_window: list[float],
                     trading_days_per_year: int = 252) -> EffectResult:
    n_in, n_out = len(in_window), len(out_window)
    mean_in = mean(in_window) if n_in else float("nan")
    mean_out = mean(out_window) if n_out else float("nan")
    sd_in = pstdev(in_window) if n_in > 1 else 0.0

    if HAVE_SCIPY and n_in > 1:
        t_stat, p_value = scipy_stats.ttest_1samp(in_window, 0.0)
    elif n_in > 1 and sd_in > 0:
        t_stat = mean_in / (sd_in / math.sqrt(n_in))
        # crude normal-approx p-value if scipy isn't available
        p_value = 2 * (1 - _norm_cdf(abs(t_stat)))
    else:
        t_stat, p_value = float("nan"), float("nan")

    sharpe_in = (mean_in / sd_in * math.sqrt(trading_days_per_year)) if sd_in > 0 else float("nan")

    return EffectResult(name=name, n_in=n_in, n_out=n_out, mean_in=mean_in, mean_out=mean_out,
                         stdev_in=sd_in, t_stat=t_stat, p_value=p_value, annualized_sharpe_in=sharpe_in)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bonferroni_alpha(base_alpha: float, n_tests: int) -> float:
    return base_alpha / max(n_tests, 1)
