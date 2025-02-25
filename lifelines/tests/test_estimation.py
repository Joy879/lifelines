# -*- coding: utf-8 -*-
import warnings

from io import StringIO, BytesIO as stringio
from collections.abc import Iterable
from itertools import combinations
from collections import Counter
import pickle
import os


import numpy as np
import pandas as pd
import pytest
from scipy.stats import weibull_min, norm, logistic, invweibull, invgamma
from autograd.scipy.special import expit
from autograd import numpy as anp

try:
    from flaky import flaky
except ImportError:
    pass

from pandas.testing import assert_frame_equal, assert_series_equal, assert_index_equal
import numpy.testing as npt

from lifelines.utils import (
    k_fold_cross_validation,
    concordance_index,
    to_long_format,
    normalize,
    to_episodic_format,
    median_survival_times,
    qth_survival_time,
)

from lifelines.exceptions import StatisticalWarning, ApproximationWarning, StatError, ConvergenceWarning, ConvergenceError
from lifelines.fitters import BaseFitter, ParametricUnivariateFitter, ParametricRegressionFitter, RegressionFitter
from lifelines.fitters.coxph_fitter import SemiParametricPHFitter

from lifelines import (
    WeibullFitter,
    ExponentialFitter,
    NelsonAalenFitter,
    KaplanMeierFitter,
    BreslowFlemingHarringtonFitter,
    CoxPHFitter,
    CoxTimeVaryingFitter,
    AalenAdditiveFitter,
    AalenJohansenFitter,
    LogNormalFitter,
    LogLogisticFitter,
    PiecewiseExponentialFitter,
    WeibullAFTFitter,
    LogNormalAFTFitter,
    LogLogisticAFTFitter,
    PiecewiseExponentialRegressionFitter,
    GeneralizedGammaFitter,
    GeneralizedGammaRegressionFitter,
    SplineFitter,
    MixtureCureFitter,
    CRCSplineFitter,
)

from lifelines.datasets import (
    load_larynx,
    load_waltons,
    load_kidney_transplant,
    load_rossi,
    load_panel_test,
    load_g3,
    load_holly_molly_polly,
    load_regression_dataset,
    load_stanford_heart_transplants,
    load_multicenter_aids_cohort_study,
    load_c_botulinum_lag_phase,
    load_diabetes,
)
from lifelines.generate_datasets import generate_hazard_rates, generate_random_lifetimes, piecewise_exponential_survival_data


@pytest.fixture
def sample_lifetimes():
    N = 100
    return (np.random.randint(1, 20, size=N), np.random.randint(2, size=N))


@pytest.fixture
def positive_sample_lifetimes():
    N = 100
    return (np.random.randint(1, 20, size=N), np.random.randint(2, size=N))


@pytest.fixture
def waltons_dataset():
    return load_waltons()


@pytest.fixture
def data_pred1():
    N = 150
    data_pred1 = pd.DataFrame()
    data_pred1["x1"] = np.random.uniform(size=N)
    data_pred1["t"] = 1 + data_pred1["x1"] + np.random.normal(0, 0.05, size=N)
    data_pred1["E"] = True
    return data_pred1


class PiecewiseExponentialFitterTesting(PiecewiseExponentialFitter):
    def __init__(self, *args, **kwargs):
        super(PiecewiseExponentialFitterTesting, self).__init__([5.0], *args, **kwargs)


class SplineFitterTesting(SplineFitter):
    def __init__(self, *args, **kwargs):
        super(SplineFitterTesting, self).__init__([0.0, 40.0], *args, **kwargs)


class CustomRegressionModelTesting(ParametricRegressionFitter):

    _FAST_MEDIAN_PREDICT = True
    _fitted_parameter_names = ["lambda_", "beta_", "rho_"]

    def __init__(self, **kwargs):
        cols = load_rossi().drop(["week", "arrest"], axis=1).columns
        self.regressors = {"lambda_": "1", "beta_": "1", "rho_": "1"}
        super(CustomRegressionModelTesting, self).__init__(**kwargs)

    def _cumulative_hazard(self, params, T, Xs):
        c = expit(anp.dot(Xs["beta_"], params["beta_"]))

        lambda_ = anp.exp(anp.dot(Xs["lambda_"], params["lambda_"]))
        rho_ = anp.exp(anp.dot(Xs["rho_"], params["rho_"]))
        cdf = 1 - anp.exp(-((T / lambda_) ** rho_))

        return -anp.log((1 - c) + c * (1 - cdf))


@pytest.fixture
def data_pred2():
    N = 150
    data_pred2 = pd.DataFrame()
    data_pred2["x1"] = np.random.uniform(size=N)
    data_pred2["x2"] = np.random.uniform(size=N)
    data_pred2["t"] = 1 + data_pred2["x1"] + data_pred2["x2"] + np.random.normal(0, 0.05, size=N)
    data_pred2["E"] = True
    return data_pred2


@pytest.fixture
def data_nus():
    data_nus = pd.DataFrame(
        [[6, 31.4], [98, 21.5], [189, 27.1], [374, 22.7], [1002, 35.7], [1205, 30.7], [2065, 26.5], [2201, 28.3], [2421, 27.9]],
        columns=["t", "x"],
    )
    data_nus["E"] = True
    return data_nus


@pytest.fixture
def rossi():
    return load_rossi()


@pytest.fixture
def regression_dataset():
    return load_regression_dataset()


@pytest.fixture
def known_parametric_univariate_fitters():
    return [
        ExponentialFitter,
        WeibullFitter,
        LogNormalFitter,
        LogLogisticFitter,
        PiecewiseExponentialFitterTesting,
        GeneralizedGammaFitter,
        SplineFitterTesting,
    ]


class TestBaseFitter:
    def test_repr_without_fitter(self):
        bf = BaseFitter()
        assert bf.__repr__() == "<lifelines.BaseFitter>"


class TestParametricUnivariateFitters:
    def test_spline_fitter_has_specific_fit_method(self):
        sp = SplineFitter([1, 2, 3])
        assert sp._scipy_fit_method == "SLSQP"

    @flaky
    def test_confidence_interval_is_expected(self):

        from autograd.scipy.special import logit
        from autograd.scipy.stats import norm

        N = 20
        U = np.random.rand(N)
        T = -(logit(-np.log(U) / 0.5) - np.random.exponential(2, N) - 7.00) / 0.50

        E = ~np.isnan(T)
        T[np.isnan(T)] = 50

        class UpperAsymptoteFitter(ParametricUnivariateFitter):

            _fitted_parameter_names = ["c_", "mu_"]

            _bounds = ((0, None), (None, None))

            def _cumulative_hazard(self, params, times):
                c, mu_ = params
                return c * norm.cdf((times - mu_) / 6.3, loc=0, scale=1)

        uaf = UpperAsymptoteFitter().fit(T, E, ci_labels=("l", "u"))
        upper = uaf.confidence_interval_.iloc[-1]["u"]
        lower = uaf.confidence_interval_.iloc[-1]["l"]
        coef, std = uaf.summary.loc["c_", ["coef", "se(coef)"]]

        assert (upper - lower) > 0
        assert abs(upper - lower) > 0.3
        assert coef - std > lower
        assert coef + std < upper

    def test_AIC_on_models(self, known_parametric_univariate_fitters):
        T = np.random.exponential(1, size=1000)

        for fitter in known_parametric_univariate_fitters:
            f = fitter().fit(T)
            assert f.AIC_ > 0
            npt.assert_allclose(f.AIC_, -2 * f.log_likelihood_ + 2 * f.summary.shape[0])

    def test_models_can_handle_really_large_duration_values(self, known_parametric_univariate_fitters):
        T1 = np.random.exponential(1e12, size=1000)
        T2 = np.random.exponential(1e12, size=1000)
        E = T1 < T2
        T = np.minimum(T1, T2)
        for fitter in known_parametric_univariate_fitters:
            fitter().fit(T, E)

    def test_models_can_handle_really_small_duration_values(self, known_parametric_univariate_fitters):
        T1 = np.random.exponential(1e-6, size=1000)
        T2 = np.random.exponential(1e-6, size=1000)
        E = T1 < T2
        T = np.minimum(T1, T2)

        for fitter in known_parametric_univariate_fitters:
            fitter().fit(T, E).print_summary(5)

    def test_models_can_handle_really_small_duration_values_for_left_censorship(self, known_parametric_univariate_fitters):
        T1 = np.random.exponential(1e-6, size=1000)
        T2 = np.random.exponential(1e-6, size=1000)
        E = T1 > T2
        T = np.maximum(T1, T2)

        for fitter in known_parametric_univariate_fitters:
            if isinstance(fitter(), PiecewiseExponentialFitterTesting):
                # not a good model since the "break" is at 5.
                continue
            fitter().fit_left_censoring(T, E)

    def test_parametric_univariate_fitters_can_print_summary(
        self, positive_sample_lifetimes, known_parametric_univariate_fitters
    ):
        for fitter in known_parametric_univariate_fitters:
            f = fitter().fit(positive_sample_lifetimes[0])
            f.summary
            f.print_summary()

    def test_parametric_univariate_fitters_has_confidence_intervals(
        self, positive_sample_lifetimes, known_parametric_univariate_fitters
    ):
        for fitter in known_parametric_univariate_fitters:
            f = fitter().fit(positive_sample_lifetimes[0])
            assert f.confidence_interval_ is not None
            assert f.confidence_interval_survival_function_ is not None
            assert f.confidence_interval_hazard_ is not None

    def test_warnings_for_problematic_cumulative_hazards(self):
        class NegativeFitter(ParametricUnivariateFitter):

            _fitted_parameter_names = ["a"]

            def _cumulative_hazard(self, params, times):
                return params[0] * (times - 0.4)

        class DecreasingFitter(ParametricUnivariateFitter):

            _fitted_parameter_names = ["a"]

            def _cumulative_hazard(self, params, times):
                return params[0] * 1 / times

        with pytest.warns(StatisticalWarning, match="positive") as w:
            NegativeFitter().fit([0.01, 0.5, 10.0, 20.0])

        with pytest.warns(StatisticalWarning, match="non-decreasing") as w:
            DecreasingFitter().fit([0.01, 0.5, 10.0, 20])

    def test_parameteric_models_all_can_do_interval_censoring(self, known_parametric_univariate_fitters):
        df = load_diabetes()
        for fitter in known_parametric_univariate_fitters:
            f = fitter().fit_interval_censoring(df["left"], df["right"])
            f.print_summary()

    def test_parameteric_models_all_can_do_interval_censoring_with_prediction(self, known_parametric_univariate_fitters):
        df = load_diabetes()
        for fitter in known_parametric_univariate_fitters:
            f = fitter().fit_interval_censoring(df["left"], df["right"])
            f.predict(3.0)

    def test_parameteric_models_fail_if_passing_in_bad_event_data(self, known_parametric_univariate_fitters):
        df = load_diabetes()
        for fitter in known_parametric_univariate_fitters:
            with pytest.raises(ValueError, match="lower_bound == upper_bound"):
                f = fitter().fit_interval_censoring(df["left"], df["right"], event_observed=np.ones_like(df["right"]))

    def test_print_summary(self, sample_lifetimes, known_parametric_univariate_fitters):
        T = np.random.exponential(1, size=100)
        for f in known_parametric_univariate_fitters:
            f = f()
            f.fit(T)
            f.print_summary(style="ascii", decimals=4, columns=["coef", "p"])
            f.print_summary(style="html", decimals=4, columns=["coef", "p"])
            f.print_summary(style="latex", decimals=4, columns=["coef", "p"])


class TestUnivariateFitters:
    @pytest.fixture
    def univariate_fitters(self):
        return [
            KaplanMeierFitter,
            NelsonAalenFitter,
            BreslowFlemingHarringtonFitter,
            ExponentialFitter,
            WeibullFitter,
            LogNormalFitter,
            LogLogisticFitter,
            PiecewiseExponentialFitterTesting,
            GeneralizedGammaFitter,
            SplineFitterTesting,
        ]

    def test_label_is_not_overwritten(self):
        fitter = WeibullFitter(label="Weibull")
        fitter.fit([1, 2, 3, 4], event_observed=[1, 1, 1, 1])
        assert fitter._label == "Weibull"

        fitter = KaplanMeierFitter(label="KM")
        fitter.fit([1, 2, 3, 4], event_observed=[1, 1, 1, 1])
        assert fitter._label == "KM"

    def test_confidence_interval_has_the_correct_order_so_plotting_doesnt_break(self, sample_lifetimes, univariate_fitters):
        T, E = sample_lifetimes
        for f in univariate_fitters:
            f = f()
            f.fit(T, E)
            assert "lower" in f.confidence_interval_.columns[0]
            assert "upper" in f.confidence_interval_.columns[1]

    def test_repr_with_fitter(self, sample_lifetimes, univariate_fitters):
        T, E = sample_lifetimes
        for f in univariate_fitters:
            f = f()
            f.fit(T, E)
            assert (
                f.__repr__()
                == """<lifelines.%s:"%s", fitted with %d total observations, %d right-censored observations>"""
                % (
                    f._class_name,
                    f._label,
                    E.shape[0],
                    E.shape[0] - E.sum(),
                )
            )

    def test_allow_dataframes(self, univariate_fitters):
        t_2d = np.random.exponential(5, size=(2000, 1)) ** 2
        t_df = pd.DataFrame(t_2d)
        for f in univariate_fitters:
            f().fit(t_2d)
            f().fit(t_df)

    def test_has_percentile_function(self, univariate_fitters, positive_sample_lifetimes):
        for fitter in univariate_fitters:
            f = fitter().fit(positive_sample_lifetimes[0])
            if hasattr(f, "survival_function_"):
                print(f)
                assert f.percentile(0.5) == f.median_survival_time_

    def test_default_alpha_is_005(self, univariate_fitters):
        for f in univariate_fitters:
            assert f().alpha == 0.05

    def test_univariate_fitters_accept_late_entries(self, positive_sample_lifetimes, univariate_fitters):
        positive_sample_lifetimes = positive_sample_lifetimes
        entries = positive_sample_lifetimes[0] - 3
        for fitter in univariate_fitters:
            f = fitter().fit(positive_sample_lifetimes[0], entry=entries)
            assert f.entry is not None

    def test_univariate_fitters_accepts_fit_options(self, positive_sample_lifetimes, univariate_fitters):
        T = positive_sample_lifetimes[0]
        for fitter in univariate_fitters:
            fitter().fit_right_censoring(T, fit_options={"tol": 0.1})
            if hasattr(fitter, "fit_left_censoring"):
                fitter().fit_left_censoring(T, fit_options={"tol": 0.1})
            if hasattr(fitter, "fit_interval_censoring"):
                fitter().fit_interval_censoring(T, T + 1, fit_options={"tol": 0.1})

    def test_univariate_fitters_with_survival_function_have_conditional_time_to_(
        self, positive_sample_lifetimes, univariate_fitters
    ):
        for fitter in univariate_fitters:

            f = fitter().fit(positive_sample_lifetimes[0])
            if hasattr(f, "survival_function_"):
                assert all(f.conditional_time_to_event_.index == f.survival_function_.index)

    def test_conditional_time_to_allows_custom_timelines(self, univariate_fitters):
        t = np.random.binomial(50, 0.4, 100)
        e = np.random.binomial(1, 0.8, 100)
        for fitter in univariate_fitters:
            f = fitter().fit(t, e, timeline=np.linspace(0, 40, 41))
            if hasattr(f, "survival_function_"):
                assert all(f.conditional_time_to_event_.index == f.survival_function_.index)

    def test_univariate_fitters_allows_one_to_change_alpha_at_fit_time(self, positive_sample_lifetimes, univariate_fitters):
        alpha = 0.1
        alpha_fit = 0.05
        for f in univariate_fitters:
            fitter = f(alpha=alpha)
            fitter.fit(positive_sample_lifetimes[0], alpha=alpha_fit)
            assert str(1 - alpha_fit) in fitter.confidence_interval_.columns[0]

            fitter.fit(positive_sample_lifetimes[0])
            assert str(1 - alpha) in fitter.confidence_interval_.columns[0]

    def test_univariate_fitters_have_a_plot_method(self, positive_sample_lifetimes, univariate_fitters):
        T = positive_sample_lifetimes[0]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(T)
            assert hasattr(fitter, "plot")

    def test_univariate_fitters_ok_if_given_timedelta(self, univariate_fitters):
        t = pd.Series([pd.to_datetime("2015-01-01 12:00"), pd.to_datetime("2015-01-02"), pd.to_datetime("2015-01-02 12:00")])
        T = pd.to_datetime("2015-01-03") - t
        for fitter in univariate_fitters:
            f = fitter().fit(T)
            try:
                npt.assert_allclose(f.timeline, 1e9 * 12 * 60 * 60 * np.array([0, 1, 2, 3]))
            except:
                npt.assert_allclose(f.timeline, 1e9 * 12 * 60 * 60 * np.array([1, 2, 3]))

    def test_univariate_fitters_okay_if_given_boolean_col_with_object_dtype(self, univariate_fitters):
        df = pd.DataFrame({"T": [1, 2, 3, 4, 5], "E": [True, True, True, True, None]})
        assert df["E"].dtype == object
        df = df.dropna()
        assert df["E"].dtype == object

        for fitter in univariate_fitters:
            with pytest.warns(UserWarning, match="convert"):
                fitter().fit(df["T"], df["E"])

    def test_predict_methods_returns_a_scalar_or_a_array_depending_on_input(self, positive_sample_lifetimes, univariate_fitters):
        T = positive_sample_lifetimes[0]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(T)
            assert not isinstance(fitter.predict(1), Iterable)
            assert isinstance(fitter.predict([1, 2]), Iterable)

    def test_predict_method_returns_exact_value_if_given_an_observed_time(self):
        T = [1, 2, 3]
        kmf = KaplanMeierFitter()
        kmf.fit(T)
        time = 1
        assert abs(kmf.predict(time) - kmf.survival_function_.iloc[time].values) < 10e-8

    def test_predict_method_returns_an_approximation_if_not_in_the_index_and_interpolate_set_to_true(self):
        T = [1, 2, 3]
        kmf = KaplanMeierFitter()
        kmf.fit(T)
        assert abs(kmf.predict(0.5, interpolate=True) - 5 / 6.0) < 10e-8
        assert abs(kmf.predict(1.9999, interpolate=True) - 0.3333666666) < 10e-8

    def test_predict_method_returns_the_previous_value_if_not_in_the_index(self):
        T = [1, 2, 3]
        kmf = KaplanMeierFitter()
        kmf.fit(T)
        assert abs(kmf.predict(1.0, interpolate=False) - 2 / 3) < 10e-8
        assert abs(kmf.predict(1.9999, interpolate=False) - 2 / 3) < 10e-8

    def test_custom_timeline_can_be_list_or_array(self, positive_sample_lifetimes, univariate_fitters):
        T, C = positive_sample_lifetimes
        timeline = [2, 3, 4.0, 1.0, 6, 5.0]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(T, C, timeline=timeline)
            if hasattr(fitter, "survival_function_"):
                with_list = fitter.survival_function_.values
                with_array = fitter.fit(T, C, timeline=np.array(timeline)).survival_function_.values
                npt.assert_array_equal(with_list, with_array)
            elif hasattr(fitter, "cumulative_hazard_"):
                with_list = fitter.cumulative_hazard_.values
                with_array = fitter.fit(T, C, timeline=np.array(timeline)).cumulative_hazard_.values
                npt.assert_array_equal(with_list, with_array)

    def test_custom_timeline(self, positive_sample_lifetimes, univariate_fitters):
        T, C = positive_sample_lifetimes
        timeline = [2, 3, 4.0, 1.0, 6, 5.0]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(T, C, timeline=timeline)
            if hasattr(fitter, "survival_function_"):
                assert sorted(timeline) == list(fitter.survival_function_.index.values)
            elif hasattr(fitter, "cumulative_hazard_"):
                assert sorted(timeline) == list(fitter.cumulative_hazard_.index.values)

    def test_label_is_a_property(self, positive_sample_lifetimes, univariate_fitters):
        label = "Test Label"
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(positive_sample_lifetimes[0], label=label)
            assert fitter._label == label
            assert fitter.confidence_interval_.columns[0] == "%s_lower_0.95" % label
            assert fitter.confidence_interval_.columns[1] == "%s_upper_0.95" % label

    def test_ci_labels(self, positive_sample_lifetimes, univariate_fitters):
        expected = ["upper", "lower"]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(positive_sample_lifetimes[0], ci_labels=expected)
            npt.assert_array_equal(fitter.confidence_interval_.columns, expected)

    def test_ci_is_not_all_nan(self, positive_sample_lifetimes, univariate_fitters):
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(positive_sample_lifetimes[0])
            try:
                fitter.print_summary()
            except:
                pass
            assert not (pd.isnull(fitter.confidence_interval_)).all().all()

    def test_lists_and_tuples_as_input(self, positive_sample_lifetimes, univariate_fitters):
        T, C = positive_sample_lifetimes
        for f in univariate_fitters:
            fitter = f()

            if isinstance(fitter, NelsonAalenFitter):
                with_array = fitter.fit(T, C).cumulative_hazard_
                with_list = fitter.fit(list(T), list(C)).cumulative_hazard_
                with_tuple = fitter.fit(tuple(T), tuple(C)).cumulative_hazard_
                assert_frame_equal(with_list, with_array)
                assert_frame_equal(with_tuple, with_array)

            else:
                with_array = fitter.fit(T, C).survival_function_
                with_list = fitter.fit(list(T), list(C)).survival_function_
                with_tuple = fitter.fit(tuple(T), tuple(C)).survival_function_
                assert_frame_equal(with_list, with_array)
                assert_frame_equal(with_tuple, with_array)

                if isinstance(fitter, ParametricUnivariateFitter):
                    with_array = fitter.fit_interval_censoring(T, T + 1, (T == T + 1)).survival_function_
                    with_list = fitter.fit_interval_censoring(list(T), list(T + 1), list((T == T + 1))).survival_function_
                    with_tuple = fitter.fit_interval_censoring(tuple(T), tuple(T + 1), tuple((T == T + 1))).survival_function_
                    assert_frame_equal(with_list, with_array)
                    assert_frame_equal(with_tuple, with_array)

                    with_array = fitter.fit_left_censoring(T, C).survival_function_
                    with_list = fitter.fit_left_censoring(list(T), list(C)).survival_function_
                    with_tuple = fitter.fit_left_censoring(tuple(T), tuple(C)).survival_function_
                    assert_frame_equal(with_list, with_array)
                    assert_frame_equal(with_tuple, with_array)

    def test_subtraction_function(self, positive_sample_lifetimes, univariate_fitters):
        T2 = np.arange(1, 50)
        for fitter in univariate_fitters:
            f1 = fitter()
            f2 = fitter()

            f1.fit(positive_sample_lifetimes[0])
            f2.fit(T2)

            result = f1.subtract(f2)
            assert result.shape[0] == (np.unique(np.concatenate((f1.timeline, f2.timeline))).shape[0])

            npt.assert_array_almost_equal(f1.subtract(f1).sum().values, 0.0)

    def test_subtract_function_with_labelled_data(self, positive_sample_lifetimes, univariate_fitters):
        T2 = np.arange(1, 50)
        for fitter in univariate_fitters:
            f1 = fitter()
            f2 = fitter()

            f1.fit(positive_sample_lifetimes[0], label="A")
            f2.fit(T2, label="B")

            result = f1.subtract(f2)
            assert result.columns == ["diff"]
            assert result.shape[1] == 1

    def test_divide_function(self, positive_sample_lifetimes, univariate_fitters):
        T2 = np.arange(1, 50)
        for fitter in univariate_fitters:
            f1 = fitter()
            f2 = fitter()

            f1.fit(positive_sample_lifetimes[0])
            f2.fit(T2)

            result = f1.divide(f2)
            assert result.shape[0] == (np.unique(np.concatenate((f1.timeline, f2.timeline))).shape[0])
            npt.assert_array_almost_equal(np.log(f1.divide(f1)).sum().values, 0.0)

    def test_divide_function_with_labelled_data(self, positive_sample_lifetimes, univariate_fitters):
        T2 = np.arange(1, 50)
        for fitter in univariate_fitters:
            f1 = fitter()
            f2 = fitter()

            f1.fit(positive_sample_lifetimes[0], label="A")
            f2.fit(T2, label="B")

            result = f1.divide(f2)
            assert result.columns == ["ratio"]
            assert result.shape[1] == 1

    def test_valueerror_is_thrown_if_alpha_out_of_bounds(self, univariate_fitters):
        for fitter in univariate_fitters:
            with pytest.raises(ValueError):
                fitter(alpha=95)

    def test_typeerror_is_thrown_if_there_is_nans_in_the_duration_col(self, univariate_fitters):
        T = np.array([1.0, 2.0, 4.0, np.nan, 8.0])
        for fitter in univariate_fitters:
            with pytest.raises(TypeError):
                fitter().fit(T)

    def test_typeerror_is_thrown_if_there_is_nans_in_the_event_col(self, univariate_fitters):
        T = np.arange(1, 5)
        E = [1, 0, None, 1, 1]
        for fitter in univariate_fitters:
            with pytest.raises(TypeError):
                fitter().fit(T, E)

    def test_pickle_serialization(self, positive_sample_lifetimes, univariate_fitters):
        T = positive_sample_lifetimes[0]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(T)

            unpickled = pickle.loads(pickle.dumps(fitter))
            dif = (fitter.durations - unpickled.durations).sum()
            assert dif == 0

    def test_dill_serialization(self, positive_sample_lifetimes, univariate_fitters):
        from dill import dumps, loads

        T = positive_sample_lifetimes[0]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(T)

            unpickled = loads(dumps(fitter))
            dif = (fitter.durations - unpickled.durations).sum()
            assert dif == 0

    def test_joblib_serialization(self, positive_sample_lifetimes, univariate_fitters):
        from joblib import dump, load

        T = positive_sample_lifetimes[0]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(T)

            dump(fitter, "filename.joblib")
            unpickled = load("filename.joblib")
            dif = (fitter.durations - unpickled.durations).sum()
            assert dif == 0

    def test_all_models_have_censoring_type(self, positive_sample_lifetimes, univariate_fitters):
        T = positive_sample_lifetimes[0]
        for f in univariate_fitters:
            fitter = f()
            fitter.fit(T)
            assert hasattr(fitter, "_censoring_type")


class TestPiecewiseExponentialFitter:
    def test_fit_with_bad_breakpoints_raises_error(self):
        with pytest.raises(ValueError):
            pwf = PiecewiseExponentialFitter(None)

        with pytest.raises(ValueError):
            pwf = PiecewiseExponentialFitter([])

        with pytest.raises(ValueError):
            pwf = PiecewiseExponentialFitter([0, 1, 2, 3])

        with pytest.raises(ValueError):
            pwf = PiecewiseExponentialFitter([1, 2, 3, np.inf])

    @flaky(max_runs=3, min_passes=1)
    def test_fit_on_simulated_data(self):
        bp = [1, 2]
        lambdas = [0.5, 0.1, 1.0]
        N = int(5 * 1e5)
        T_actual = piecewise_exponential_survival_data(N, bp, lambdas)
        T_censor = piecewise_exponential_survival_data(N, bp, lambdas)
        T = np.minimum(T_actual, T_censor)
        E = T_actual < T_censor

        pwf = PiecewiseExponentialFitter(bp).fit(T, E)
        npt.assert_allclose(pwf.summary.loc["lambda_0_", "coef"], 1 / 0.5, rtol=0.01)
        npt.assert_allclose(pwf.summary.loc["lambda_1_", "coef"], 1 / 0.1, rtol=0.01)
        npt.assert_allclose(pwf.summary.loc["lambda_2_", "coef"], 1 / 1.0, rtol=0.01)


class TestLogNormalFitter:
    @pytest.fixture()
    def lnf(self):
        return LogNormalFitter()

    def test_lognormal_model_has_sensible_interval_censored_initial_values_for_data_with_lots_of_infs(self, lnf):
        left = [1, 0, 2, 5, 4]
        right = [np.inf, np.inf, np.inf, 5, 6]
        lnf.fit_interval_censoring(left, right)
        assert lnf._initial_values[0] < 10
        assert lnf._initial_values[1] < 10

    def test_fit(self, lnf):
        T = np.exp(np.random.randn(100000))
        E = np.ones_like(T)
        lnf.fit(T, E)
        assert abs(lnf.mu_) < 0.1
        assert abs(lnf.sigma_ - 1) < 0.1

    def test_lognormal_model_does_not_except_negative_or_zero_values(self, lnf):
        T = [0, 1, 2, 4, 5]
        with pytest.raises(ValueError):
            lnf.fit(T)

        T[0] = -1
        with pytest.raises(ValueError):
            lnf.fit(T)

    def test_cumulative_hazard_doesnt_fail(self, lnf):
        T = np.exp(np.random.randn(100))
        lnf.fit(T)
        results = lnf.cumulative_hazard_at_times([1, 2, 3])
        assert results.shape[0] == 3

        results = lnf.cumulative_hazard_at_times(pd.Series([1, 2, 3]))
        assert results.shape[0] == 3

        results = lnf.cumulative_hazard_at_times(1)
        assert results.shape[0] == 1

    def test_lnf_inference(self, lnf):
        N = 250000
        mu = 3 * np.random.randn()
        sigma = np.random.uniform(0.1, 3.0)

        X, C = np.exp(sigma * np.random.randn(N) + mu), np.exp(np.random.randn(N) + mu)
        E = X <= C
        T = np.minimum(X, C)

        lnf.fit(T, E)

        assert abs(mu - lnf.mu_) < 0.05
        assert abs(sigma - lnf.sigma_) < 0.05
        assert abs(lnf.median_survival_time_ / np.percentile(X, 50) - 1) < 0.05

    def test_lnf_inference_with_large_sigma(self, lnf):
        N = 250000
        mu = 4.94
        sigma = 12

        X, C = np.exp(sigma * np.random.randn(N) + mu), np.exp(np.random.randn(N) + mu)
        E = X <= C
        T = np.minimum(X, C)

        lnf.fit(T, E)

        assert abs(mu / lnf.mu_ - 1) < 0.05
        assert abs(sigma / lnf.sigma_ - 1) < 0.05

    def test_lnf_inference_with_small_sigma(self, lnf):
        N = 25000
        mu = 3
        sigma = 0.04

        X, C = np.exp(sigma * np.random.randn(N) + mu), np.exp(np.random.randn(N) + mu)
        E = X <= C
        T = np.minimum(X, C)

        lnf.fit(T, E)

        assert abs(mu / lnf.mu_ - 1) < 0.05
        assert abs(sigma / lnf.sigma_ - 1) < 0.05

    def test_lnf_inference_with_really_small_sigma(self, lnf):
        N = 250000
        mu = 3 * np.random.randn()
        sigma = 0.02

        X, C = np.exp(sigma * np.random.randn(N) + mu), np.exp(np.random.randn(N) + mu)
        E = X <= C
        T = np.minimum(X, C)

        lnf.fit(T, E)

        assert abs(mu / lnf.mu_ - 1) < 0.05
        assert abs(sigma / lnf.sigma_ - 1) < 0.05

    def test_lnf_inference_no_censorship(self, lnf):
        N = 1000000
        mu = 10 * np.random.randn()
        sigma = np.random.exponential(10)

        T = np.exp(sigma * np.random.randn(N) + mu)

        lnf.fit(T)

        assert abs(mu / lnf.mu_ - 1) < 0.1
        assert abs(sigma / lnf.sigma_ - 1) < 0.1


class TestLogLogisticFitter:
    @pytest.fixture()
    def llf(self):
        return LogLogisticFitter()

    def test_loglogistic_model_has_sensible_interval_censored_initial_values_for_data_with_lots_of_infs(self, llf):
        left = [1, 0, 2, 5, 4]
        right = [np.inf, np.inf, np.inf, 5, 6]
        llf.fit_interval_censoring(left, right)
        assert llf._initial_values[0] < 10
        assert llf._initial_values[1] < 10

    def test_loglogistic_model_does_not_except_negative_or_zero_values(self, llf):

        T = [0, 1, 2, 4, 5]
        with pytest.raises(ValueError):
            llf.fit(T)

        T[0] = -1
        with pytest.raises(ValueError):
            llf.fit(T)

    def test_llf_simple_inference(self, llf):
        from scipy.stats import fisk

        T = fisk.rvs(1, scale=1, size=60000)
        llf.fit(T)
        assert abs(llf.alpha_ - 1) < 0.05
        assert abs(llf.beta_ - 1) < 0.05

    def test_llf_less_simple_inference(self, llf):
        from scipy.stats import fisk

        scale = 0.3
        c = 5.4
        T = fisk.rvs(c, scale=scale, size=60000)
        llf.fit(T)
        assert abs(llf.alpha_ - scale) < 0.05
        assert abs(llf.beta_ - c) < 0.05

    def test_llf_less_simple_inference_with_censorship(self, llf):
        from scipy.stats import fisk

        scale = 0.3
        c = 5.4
        T = fisk.rvs(c, scale=scale, size=120000)
        C = fisk.rvs(c, scale=scale, size=120000)
        E = T < C
        T = np.minimum(T, C)
        assert 1 > E.mean() > 0

        llf.fit(T, E)
        assert abs(llf.alpha_ - scale) < 0.05
        assert abs(llf.beta_ - c) < 0.05

    def test_llf_large_values(self, llf):
        from scipy.stats import fisk

        scale = 20
        c = 50
        T = fisk.rvs(c, scale=scale, size=100000)
        C = fisk.rvs(c, scale=scale, size=100000)
        E = T < C
        T = np.minimum(T, C)

        assert 1 > E.mean() > 0

        llf.fit(T, E)
        assert abs(llf.alpha_ / scale - 1) < 0.05
        assert abs(llf.beta_ / c - 1) < 0.05

    @pytest.mark.xfail
    def test_llf_small_values(self, llf):
        from scipy.stats import fisk

        scale = 0.02
        c = 0.05
        T = fisk.rvs(c, scale=scale, size=100000)
        C = fisk.rvs(c, scale=scale, size=100000)
        E = T < C
        T = np.minimum(T, C)

        assert 1 > E.mean() > 0

        llf.fit(T, E)
        assert abs(llf.alpha_ - scale) < 0.02
        assert abs(llf.beta_ - c) < 0.02


class TestWeibullFitter:
    @pytest.mark.skip("skipping due to url read error")
    def test_unstable_data(self):
        data = pd.read_csv("https://raw.githubusercontent.com/scotty269/lifelines_test/master/my_data.csv")
        T = data["T"]
        E = data["E"]
        assert abs(WeibullFitter().fit(T, E).log_likelihood_ - LogNormalFitter().fit(T, E).log_likelihood_) < 0.5

    @flaky(max_runs=3, min_passes=2)
    @pytest.mark.parametrize("N", [750, 1500])
    def test_left_censorship_inference(self, N):
        T_actual = 0.5 * np.random.weibull(5, size=N)

        MIN_0 = np.percentile(T_actual, 5)
        MIN_1 = np.percentile(T_actual, 10)
        MIN_2 = np.percentile(T_actual, 30)
        MIN_3 = np.percentile(T_actual, 50)

        T = T_actual.copy()
        ix = np.random.randint(4, size=N)

        T = np.where(ix == 0, np.maximum(T, MIN_0), T)
        T = np.where(ix == 1, np.maximum(T, MIN_1), T)
        T = np.where(ix == 2, np.maximum(T, MIN_2), T)
        T = np.where(ix == 3, np.maximum(T, MIN_3), T)
        E = T_actual == T

        wf = WeibullFitter().fit_left_censoring(T, E)

        assert wf.summary.loc["rho_", "coef lower 95%"] < 5 < wf.summary.loc["rho_", "coef upper 95%"]
        assert wf.summary.loc["lambda_", "coef lower 95%"] < 0.5 < wf.summary.loc["lambda_", "coef upper 95%"]

    def test_weibull_with_delayed_entries(self):
        # note the the independence of entry and final time is really important
        # (also called non-informative)
        # for example, the following doesn't work
        # D = np.random.rand(15000) * T

        wf = WeibullFitter()
        T = np.random.exponential(10, 350000)
        D = np.random.exponential(10, 350000)

        keep = T > D
        T = T[keep]
        D = D[keep]

        wf = WeibullFitter().fit(T, entry=D)

        assert np.abs(wf.lambda_ / 10.0 - 1) < 0.01

    def test_weibull_fit_returns_float_timelines(self):
        wf = WeibullFitter()
        T = np.linspace(0.1, 10)
        wf.fit(T)
        npt.assert_array_equal(wf.timeline, T)
        npt.assert_array_equal(wf.survival_function_.index.values, T)

    def test_weibull_model_does_not_accept_negative_or_zero_values(self):
        wf = WeibullFitter()

        T = [0, 1, 2, 4, 5]
        with pytest.raises(ValueError):
            wf.fit(T)

        T[0] = -1
        with pytest.raises(ValueError):
            wf.fit(T)

    def test_exponential_data_produces_correct_inference_no_censorship(self):
        wf = WeibullFitter()
        N = 600000
        T = 5 * np.random.exponential(1, size=N) ** 2
        wf.fit(T)
        assert abs(wf.rho_ - 0.5) < 0.01
        assert abs(wf.lambda_ / 5 - 1) < 0.01
        assert abs(wf.median_survival_time_ - 5 * np.log(2) ** 2) < 0.1  # worse convergence
        assert abs(wf.median_survival_time_ - np.median(T)) < 0.1

    def test_exponential_data_produces_correct_inference_with_censorship(self):
        wf = WeibullFitter()
        N = 80000
        factor = 5
        T = factor * np.random.exponential(1, size=N)
        T_ = factor * np.random.exponential(1, size=N)
        wf.fit(np.minimum(T, T_), (T < T_))
        assert abs(wf.rho_ - 1.0) < 0.05
        assert abs(wf.lambda_ / factor - 1) < 0.05
        assert abs(wf.median_survival_time_ - factor * np.log(2)) < 0.1

    def test_convergence_completes_for_ever_increasing_data_sizes(self):
        wf = WeibullFitter()
        rho = 5
        lambda_ = 1.0 / 2
        for N in [10, 50, 500, 5000, 50000]:
            T = np.random.weibull(rho, size=N) * lambda_
            wf.fit(T)
            assert abs(1 - wf.rho_ / rho) < 5 / np.sqrt(N)
            assert abs(1 - wf.lambda_ / lambda_) < 5 / np.sqrt(N)

    def test_interval_censoring_with_excepted_results(self):

        df = pd.DataFrame()
        df["left"] = [0.1, 30.1, 60.1, 90.1, 120.1, 150.1, 180.1, 210.1]
        df["right"] = [30, 60, 90, 120, 150, 180, 210, np.inf]
        df["Observed"] = False  # Means exact fail time is not known
        df["#Units"] = [15, 17, 19, 26, 19, 43, 20, 15356]

        df["left"] = df["left"]
        df["right"] = df["right"]

        wf = WeibullFitter()
        wf.fit_interval_censoring(df["left"], df["right"], event_observed=df["Observed"], weights=df["#Units"])

        npt.assert_allclose(wf.rho_, 1.272946, rtol=1e-3)
        npt.assert_allclose(wf.lambda_, 7646.68135, rtol=1e-3)

    def test_interval_censoring_against_reliasoft(self):
        x_left = [0.1, 30, 60, 90, 120, 150, 180, 210, 240]
        x_right = [30, 60, 90, 120, 150, 180, 210, 240, np.inf]
        number = [2, 5, 1, 9, 6, 6, 5, 2, 2645]
        censor = [0, 0, 0, 0, 0, 0, 0, 0, 0]

        wf = WeibullFitter()
        wf.fit_interval_censoring(x_left, x_right, event_observed=censor, weights=number)

        npt.assert_allclose(wf.rho_, 1.215, rtol=1e-2)
        npt.assert_allclose(wf.lambda_, 8270.88, rtol=1e-2)


class TestGeneralizedGammaFitter:
    @flaky
    def test_exponential_data_inference(self):
        T = np.random.exponential(1.0, size=20000)
        gg = GeneralizedGammaFitter().fit(T)
        gg.print_summary()
        assert gg.summary.loc["lambda_"]["coef lower 95%"] < 1 < gg.summary.loc["lambda_"]["coef upper 95%"]
        assert gg.summary.loc["ln_sigma_"]["coef lower 95%"] < 0 < gg.summary.loc["ln_sigma_"]["coef upper 95%"]

    def test_weibull_data_inference(self):
        T = 5 * np.random.exponential(1, size=10000) ** 0.5
        gg = GeneralizedGammaFitter().fit(T)
        gg.print_summary()
        assert gg.summary.loc["lambda_"]["coef lower 95%"] < 1 < gg.summary.loc["lambda_"]["coef upper 95%"]

    def test_gamma_data_inference(self):
        T = np.random.gamma(shape=4, scale=0.5, size=15000)
        gg = GeneralizedGammaFitter().fit(T)
        gg.print_summary()
        assert abs(gg.summary.loc["lambda_", "coef"] - np.exp(gg.summary.loc["ln_sigma_", "coef"])) < 0.15

    def test_lognormal_data_inference(self):
        T = np.exp(np.random.randn(20000))
        gg = GeneralizedGammaFitter().fit(T)
        gg.print_summary(4)
        assert abs(gg.summary.loc["lambda_"]["coef"]) < 0.05

    @flaky
    def test_inverse_weibull_inference(self):
        T = invweibull(5).rvs(10000)
        gg = GeneralizedGammaFitter().fit(T)
        gg.print_summary(4)
        assert abs(gg.summary.loc["lambda_"]["coef"] - -1.0) < 0.05

    def test_inverse_gamma_inference(self):
        T = invgamma(0.5).rvs(20000)
        gg = GeneralizedGammaFitter().fit(T)
        gg.print_summary(4)
        assert abs(gg.summary.loc["lambda_", "coef"] - -np.exp(gg.summary.loc["ln_sigma_", "coef"])) < 0.15

    def test_against_reliability_software(self):
        # From http://reliawiki.org/index.php/The_Generalized_Gamma_Distribution
        T = [
            17.88,
            28.92,
            33,
            41.52,
            42.12,
            45.6,
            48.4,
            51.84,
            51.96,
            54.12,
            55.56,
            67.8,
            68.64,
            68.64,
            68.88,
            84.12,
            93.12,
            98.64,
            105.12,
            105.84,
            127.92,
            128.04,
            173.4,
        ]

        gg = GeneralizedGammaFitter().fit(T)
        npt.assert_allclose(gg.summary.loc["mu_", "coef"], 4.23064, rtol=0.001)
        npt.assert_allclose(gg.summary.loc["lambda_", "coef"], 0.307639, rtol=1e-3)
        npt.assert_allclose(np.exp(gg.summary.loc["ln_sigma_", "coef"]), 0.509982, rtol=1e-3)


class TestExponentialFitter:
    def test_fit_computes_correct_lambda_(self):
        T = np.array([10, 10, 10, 10], dtype=float)
        E = np.array([1, 1, 1, 0], dtype=float)
        enf = ExponentialFitter()
        enf.fit(T, E)
        assert abs(enf.lambda_ - (T.sum() / E.sum())) < 1e-4

    def test_fit_computes_correct_asymptotic_variance(self):
        N = 5000
        T = np.random.exponential(size=N)
        C = np.random.exponential(size=N)
        E = T < C
        T = np.minimum(T, C)
        enf = ExponentialFitter()
        enf.fit(T, E)
        assert abs(enf.summary.loc["lambda_", "se(coef)"] ** 2 - (T.sum() / E.sum()) ** 2 / N) < 1e-3


class TestKaplanMeierFitter:
    def kaplan_meier(self, lifetimes, observed=None):
        lifetimes_counter = Counter(lifetimes)
        km = np.zeros((len(list(lifetimes_counter.keys())), 1))
        ordered_lifetimes = np.sort(list(lifetimes_counter.keys()))
        N = len(lifetimes)
        v = 1.0
        n = N * 1.0
        for i, t in enumerate(ordered_lifetimes):
            if observed is not None:
                ix = lifetimes == t
                c = sum(1 - observed[ix])
                if n != 0:
                    v *= 1 - (lifetimes_counter.get(t) - c) / n
                n -= lifetimes_counter.get(t)
            else:
                v *= 1 - lifetimes_counter.get(t) / n
                n -= lifetimes_counter.get(t)
            km[i] = v
        if lifetimes_counter.get(0) is None:
            km = np.insert(km, 0, 1.0)
        return km.reshape(len(km), 1)

    def test_kmf_overflow_error(self):
        N = int(1e6)
        T = np.random.exponential(size=N)
        E = T < 0.001
        kmf = KaplanMeierFitter()
        kmf.fit(T, E)
        assert True

    def test_left_truncation_against_Cole_and_Hudgens(self):
        df = load_multicenter_aids_cohort_study()
        kmf = KaplanMeierFitter()
        kmf.fit(df["T"], event_observed=df["D"], entry=df["W"])

        # the papers event table only looks at times when the individuals die
        event_table = kmf.event_table[kmf.event_table["observed"] > 0]

        assert event_table.shape[0] == 26
        assert event_table.loc[0.269, "at_risk"] == 42
        assert event_table.loc[0.791, "at_risk"] == 44
        assert event_table.loc[4.688, "at_risk"] == 11

        assert kmf.survival_function_.loc[0.791, "KM_estimate"] == 0.9540043290043292
        assert abs(kmf.median_survival_time_ - 3) < 0.1

    def test_kaplan_meier_no_censorship(self, sample_lifetimes):
        T, _ = sample_lifetimes
        kmf = KaplanMeierFitter()
        kmf.fit(T)
        npt.assert_almost_equal(kmf.survival_function_.values, self.kaplan_meier(T))

    def test_kaplan_meier_with_censorship(self, sample_lifetimes):
        T, C = sample_lifetimes
        kmf = KaplanMeierFitter()
        kmf.fit(T, C)
        npt.assert_almost_equal(kmf.survival_function_.values, self.kaplan_meier(T, C))

    def test_stat_error_is_raised_if_too_few_early_deaths(self):
        observations = np.array(
            [1, 1, 1, 22, 30, 28, 32, 11, 14, 36, 31, 33, 33, 37, 35, 25, 31, 22, 26, 24, 35, 34, 30, 35, 40, 39, 2]
        )
        births = observations - 1
        kmf = KaplanMeierFitter()
        with pytest.raises(StatError):
            kmf.fit(observations, entry=births)

    def test_sort_doesnt_affect_kmf(self, sample_lifetimes):
        T, _ = sample_lifetimes
        kmf = KaplanMeierFitter()
        assert_frame_equal(kmf.fit(T).survival_function_, kmf.fit(sorted(T)).survival_function_)

    def test_passing_in_left_censorship_creates_a_cumulative_density(self, sample_lifetimes):
        T, C = sample_lifetimes
        kmf = KaplanMeierFitter()
        kmf.fit_left_censoring(T, C)
        assert hasattr(kmf, "cumulative_density_")
        assert hasattr(kmf, "plot_cumulative_density")

    def test_kmf_left_censored_data_stats(self):
        # from http://www.public.iastate.edu/~pdixon/stat505/Chapter%2011.pdf
        T = [3, 5, 5, 5, 6, 6, 10, 12]
        C = [1, 0, 0, 1, 1, 1, 0, 1]
        kmf = KaplanMeierFitter()
        kmf.fit_left_censoring(T, C)

        actual = kmf.cumulative_density_[kmf._label].values
        npt.assert_allclose(actual, np.array([0, 0.437500, 0.5833333, 0.875, 0.875, 1]))

    def test_shifting_durations_doesnt_affect_survival_function_values(self):
        T = np.random.exponential(10, size=100)
        kmf = KaplanMeierFitter()
        expected = kmf.fit(T).survival_function_.values

        T_shifted = T + 100
        npt.assert_allclose(expected, kmf.fit(T_shifted).survival_function_.values)

        T_shifted = T - 50
        npt.assert_allclose(expected[1:], kmf.fit(T_shifted).survival_function_.values)

        T_shifted = T - 200
        npt.assert_allclose(expected[1:], kmf.fit(T_shifted).survival_function_.values)

    def test_kmf_survival_curve_output_against_R(self):
        df = load_g3()
        ix = df["group"] == "RIT"
        kmf = KaplanMeierFitter()

        expected = np.array([[0.909, 0.779]]).T
        kmf.fit(df.loc[ix]["time"], df.loc[ix]["event"], timeline=[25, 53])
        npt.assert_allclose(kmf.survival_function_.values, expected, rtol=10e-3)

        expected = np.array([[0.833, 0.667, 0.5, 0.333]]).T
        kmf.fit(df.loc[~ix]["time"], df.loc[~ix]["event"], timeline=[9, 19, 32, 34])
        npt.assert_allclose(kmf.survival_function_.values, expected, rtol=10e-3)

    @pytest.mark.xfail()
    def test_kmf_survival_curve_output_against_R_super_accurate(self):
        df = load_g3()
        ix = df["group"] == "RIT"
        kmf = KaplanMeierFitter()

        expected = np.array([[0.909, 0.779]]).T
        kmf.fit(df.loc[ix]["time"], df.loc[ix]["event"], timeline=[25, 53])
        npt.assert_allclose(kmf.survival_function_.values, expected, rtol=10e-4)

        expected = np.array([[0.833, 0.667, 0.5, 0.333]]).T
        kmf.fit(df.loc[~ix]["time"], df.loc[~ix]["event"], timeline=[9, 19, 32, 34])
        npt.assert_allclose(kmf.survival_function_.values, expected, rtol=10e-4)

    def test_kmf_confidence_intervals_output_against_R(self):
        # this uses conf.type = 'log-log'
        df = load_g3()
        ix = df["group"] != "RIT"
        kmf = KaplanMeierFitter()
        kmf.fit(df.loc[ix]["time"], df.loc[ix]["event"], timeline=[9, 19, 32, 34])

        expected_lower_bound = np.array([0.2731, 0.1946, 0.1109, 0.0461])
        npt.assert_allclose(kmf.confidence_interval_["KM_estimate_lower_0.95"].values, expected_lower_bound, rtol=10e-4)

        expected_upper_bound = np.array([0.975, 0.904, 0.804, 0.676])
        npt.assert_allclose(kmf.confidence_interval_["KM_estimate_upper_0.95"].values, expected_upper_bound, rtol=10e-4)

    def test_kmf_does_not_drop_to_zero_if_last_point_is_censored(self):
        T = np.arange(0, 50, 0.5)
        E = np.random.binomial(1, 0.7, 100)
        E[np.argmax(T)] = 0
        kmf = KaplanMeierFitter()
        kmf.fit(T, E)
        assert kmf.survival_function_["KM_estimate"].iloc[-1] > 0

    def test_adding_weights_to_KaplanMeierFitter(self):
        n = 100
        df = pd.DataFrame()
        df["T"] = np.random.binomial(40, 0.5, n)
        df["E"] = np.random.binomial(1, 0.9, n)

        kmf_no_weights = KaplanMeierFitter().fit(df["T"], df["E"])

        df_grouped = df.groupby(["T", "E"]).size().reset_index()
        kmf_w_weights = KaplanMeierFitter().fit(df_grouped["T"], df_grouped["E"], weights=df_grouped[0])

        assert_frame_equal(kmf_w_weights.survival_function_, kmf_no_weights.survival_function_)

    def test_weights_can_be_floats(self):
        n = 100
        T = np.random.binomial(40, 0.5, n)
        E = np.random.binomial(1, 0.9, n)
        with pytest.warns(StatisticalWarning) as w:
            kmf = KaplanMeierFitter().fit(T, E, weights=np.random.random(n))
            assert True

    def test_weights_with_unaligned_index(self):
        df = pd.DataFrame(index=[5, 6, 7, 8])
        df["t"] = [0.6, 0.4, 0.8, 0.9]
        df["y"] = [0, 1, 1, 0]
        df["w"] = [1.5, 2, 0.8, 0.9]
        with pytest.warns(StatisticalWarning) as w:
            kmf = KaplanMeierFitter().fit(durations=df["t"], event_observed=df["y"], weights=df["w"])
            a = list(kmf.survival_function_.KM_estimate)
            assert a == [1.0, 0.6153846153846154, 0.6153846153846154, 0.32579185520362, 0.32579185520362]

    def test_late_entry_with_almost_tied_entry_and_death_against_R(self):
        entry = [1.9, 0, 0, 0, 0]
        T = [2, 10, 5, 4, 3]
        kmf = KaplanMeierFitter()
        kmf.fit(T, entry=entry)

        expected = [1.0, 1.0, 0.8, 0.6, 0.4, 0.2, 0.0]
        npt.assert_allclose(kmf.survival_function_.values.reshape(7), expected)

    def test_late_entry_with_against_R(self):
        entry = [1, 2, 4, 0, 0]
        T = [2, 10, 5, 4, 3]
        kmf = KaplanMeierFitter()
        kmf.fit(T, entry=entry)

        expected = [1.0, 1.0, 0.667, 0.444, 0.222, 0.111, 0.0]
        npt.assert_allclose(kmf.survival_function_.values.reshape(7), expected, rtol=1e-2)

    def test_kmf_has_both_survival_function_and_cumulative_density(self):
        # right censoring
        kmf = KaplanMeierFitter().fit_right_censoring(np.arange(100))
        assert hasattr(kmf, "survival_function_")
        assert hasattr(kmf, "plot_survival_function")
        assert hasattr(kmf, "confidence_interval_survival_function_")
        assert_frame_equal(kmf.confidence_interval_survival_function_, kmf.confidence_interval_)

        assert hasattr(kmf, "cumulative_density_")
        assert hasattr(kmf, "plot_cumulative_density")
        assert hasattr(kmf, "confidence_interval_cumulative_density_")

        # left censoring
        kmf = KaplanMeierFitter().fit_left_censoring(np.arange(100))
        assert hasattr(kmf, "survival_function_")
        assert hasattr(kmf, "plot_survival_function")
        assert hasattr(kmf, "confidence_interval_survival_function_")

        assert hasattr(kmf, "cumulative_density_")
        assert hasattr(kmf, "plot_cumulative_density")
        assert hasattr(kmf, "confidence_interval_cumulative_density_")
        assert_frame_equal(kmf.confidence_interval_survival_function_, kmf.confidence_interval_)

    def test_late_entry_with_tied_entry_and_death(self):
        np.random.seed(101)

        Ct = 10.0

        n = 10000
        df = pd.DataFrame()
        df["id"] = [i for i in range(n)]
        df["t"] = np.ceil(np.random.weibull(1, size=n) * 5)
        df["t_cens"] = np.ceil(np.random.weibull(1, size=n) * 3)
        df["t_enter"] = np.floor(np.random.weibull(1.5, size=n) * 2)
        df["ft"] = 10
        df["t_out"] = np.min(df[["t", "t_cens", "ft"]], axis=1).astype(int)
        df["d"] = (np.where(df["t"] <= Ct, 1, 0)) * (np.where(df["t"] <= df["t_cens"], 1, 0))
        df["c"] = (np.where(df["t_cens"] <= Ct, 1, 0)) * (np.where(df["t_cens"] < df["t"], 1, 0))
        df["y"] = (
            (np.where(df["t"] > df["t_enter"], 1, 0))
            * (np.where(df["t_cens"] > df["t_enter"], 1, 0))
            * (np.where(Ct > df["t_enter"], 1, 0))
        )
        dfo = df.loc[df["y"] == 1].copy()  # "observed data"

        # Fitting KM to full data
        km1 = KaplanMeierFitter()
        km1.fit(df["t_out"], event_observed=df["d"])
        rf = pd.DataFrame(index=km1.survival_function_.index)
        rf["KM_true"] = km1.survival_function_

        # Fitting KM to "observed" data
        km2 = KaplanMeierFitter()
        km2.fit(dfo["t_out"], entry=dfo["t_enter"], event_observed=dfo["d"])
        rf["KM_lifelines_latest"] = km2.survival_function_

        # Version of KM where late entries occur after
        rf["KM_lateenterafter"] = np.cumprod(
            1 - (km2.event_table.observed / (km2.event_table.at_risk - km2.event_table.entrance))
        )

        # drop the first NA from comparison
        rf = rf.dropna()

        npt.assert_allclose(rf["KM_true"].values, rf["KM_lateenterafter"].values, rtol=10e-2)
        npt.assert_allclose(rf["KM_lifelines_latest"].values, rf["KM_lateenterafter"].values, rtol=10e-2)
        npt.assert_allclose(rf["KM_lifelines_latest"].values, rf["KM_true"].values, rtol=10e-2)

    def test_interval_censoring_to_r_test_0_and_inf(self):
        kmf = KaplanMeierFitter()
        left = [0, 8, 8, 7, 7, 17, 37, 46, 46, 45]
        right = [7, 8, 10, 16, 14, np.inf, 44, np.inf, np.inf, np.inf]

        kmf.fit_interval_censoring(left, right)

        npt.assert_allclose(kmf.survival_function_.loc[0.0].values, np.array([1.0, 1.0]))
        npt.assert_allclose(kmf.survival_function_.loc[7.0].values, np.array([1.0, 1.0]))
        npt.assert_allclose(kmf.survival_function_.loc[8.0].values, np.array([0.83333, 0.83333]), rtol=1e-3)
        npt.assert_allclose(kmf.survival_function_.loc[10.0].values, np.array([0.5, 0.5]))
        npt.assert_allclose(kmf.survival_function_.loc[44.0].values, np.array([0.5, 0.375]), rtol=1e-3)
        npt.assert_allclose(kmf.survival_function_.iloc[-1].values, np.array([0.375, 0.0]), atol=1e-3)

    def _test_ties_and_overlapping_intervals(self):
        kmf = KaplanMeierFitter()
        left = [6, 7, 8, 7, 5]
        right = [7, 8, 10, 16, 20]

        kmf.fit_interval_censoring(left, right)

        npt.assert_allclose(kmf.survival_function_.loc[5.0].values, np.array([1.0, 1.0]))
        npt.assert_allclose(kmf.survival_function_.loc[6.0].values, np.array([1.0, 1.0]))
        npt.assert_allclose(kmf.survival_function_.loc[7.0].values, np.array([1.0, 1.0]))
        npt.assert_allclose(kmf.survival_function_.loc[8.0].values, np.array([0.5, 0.5]))
        npt.assert_allclose(kmf.survival_function_.loc[10.0].values, np.array([0.0, 0.0]))

    def test_interval_censoring_with_custom_index(self):
        kmf = KaplanMeierFitter()
        left = [6, 7, 8, 7, 5]
        right = [7, 8, 10, 16, 20]

        kmf.fit_interval_censoring(left, right, timeline=np.arange(10))
        npt.assert_allclose(kmf.survival_function_.index.values, np.arange(10))

    def test_interval_censoring_with_weights(self):
        kmf = KaplanMeierFitter()
        left = [6, 7, 8, 7, 5]
        right = [7, 8, 10, 16, 20]

        kmf.fit_interval_censoring(left, right, timeline=np.arange(10))
        npt.assert_allclose(kmf.survival_function_.index.values, np.arange(10))

    def test_interval_censoring_fit_against_R(self):
        """
        library(icenReg)

        left  <-  c(0, 4, 2, 0, 2, 5)
        right <- c(1, 6, 6, 3, 4, 7)

        data = data.frame(left,right)

        fit <- ic_np(cbind(left, right) ~ 0, data=data, B=c(1,1))
        plot(fit)
        fit$T_bull_Intervals
        getSCurves(fit)
        """
        left = np.array([0, 4, 2, 0, 2, 5])
        right = np.array([1, 6, 6, 3, 4, 7])

        kmf_interval = KaplanMeierFitter()
        kmf_interval.fit_interval_censoring(left, right)

        npt.assert_allclose(kmf_interval.survival_function_.loc[0, "NPMLE_estimate_upper"], 1.0, rtol=1e-2)
        npt.assert_allclose(kmf_interval.survival_function_.loc[0, "NPMLE_estimate_lower"], 1.0, rtol=1e-2)

        npt.assert_allclose(kmf_interval.survival_function_.loc[1, "NPMLE_estimate_upper"], 1.0, rtol=1e-2)
        npt.assert_allclose(kmf_interval.survival_function_.loc[1, "NPMLE_estimate_lower"], 0.75, rtol=1e-2)

        npt.assert_allclose(kmf_interval.survival_function_.loc[2, "NPMLE_estimate_upper"], 0.75, rtol=1e-2)
        npt.assert_allclose(kmf_interval.survival_function_.loc[2, "NPMLE_estimate_lower"], 0.75, rtol=1e-2)

        npt.assert_allclose(kmf_interval.survival_function_.loc[4, "NPMLE_estimate_upper"], 0.5, rtol=1e-2)
        npt.assert_allclose(kmf_interval.survival_function_.loc[4, "NPMLE_estimate_lower"], 0.5, rtol=1e-2)

        npt.assert_allclose(kmf_interval.survival_function_.loc[6, "NPMLE_estimate_upper"], 0.375, rtol=1e-2)
        npt.assert_allclose(kmf_interval.survival_function_.loc[6, "NPMLE_estimate_lower"], 0.0, rtol=1e-2)

    def test_really_late_entrance(self):
        T = [1, 2, 3, 1, 2, 6]
        entries = [0, 0, 0, 0, 0, 5]

        kmf = KaplanMeierFitter()
        kmf.fit(T, entry=entries)
        assert np.all((kmf.survival_function_.diff().dropna() <= 0))


class TestNelsonAalenFitter:
    def nelson_aalen(self, lifetimes, observed=None):
        lifetimes_counter = Counter(lifetimes)
        na = np.zeros((len(list(lifetimes_counter.keys())), 1))
        ordered_lifetimes = np.sort(list(lifetimes_counter.keys()))
        N = len(lifetimes)
        v = 0.0
        n = N * 1.0
        for i, t in enumerate(ordered_lifetimes):
            if observed is not None:
                ix = lifetimes == t
                c = sum(1 - observed[ix])
                if n != 0:
                    v += (lifetimes_counter.get(t) - c) / n
                n -= lifetimes_counter.get(t)
            else:
                v += lifetimes_counter.get(t) / n
                n -= lifetimes_counter.get(t)
            na[i] = v
        if lifetimes_counter.get(0) is None:
            na = np.insert(na, 0, 0.0)
        return na.reshape(len(na), 1)

    def test_cumulative_hazard_at_times(self, sample_lifetimes):
        T, _ = sample_lifetimes
        naf = NelsonAalenFitter(nelson_aalen_smoothing=False)
        naf.fit(T)
        naf.cumulative_hazard_at_times([0.5, 0.9, 1.0])

    def test_nelson_aalen_no_censorship(self, sample_lifetimes):
        T, _ = sample_lifetimes
        naf = NelsonAalenFitter(nelson_aalen_smoothing=False)
        naf.fit(T)
        npt.assert_almost_equal(naf.cumulative_hazard_.values, self.nelson_aalen(T))

    def test_censor_nelson_aalen(self, sample_lifetimes):
        T, C = sample_lifetimes
        naf = NelsonAalenFitter(nelson_aalen_smoothing=False)
        naf.fit(T, C)
        npt.assert_almost_equal(naf.cumulative_hazard_.values, self.nelson_aalen(T, C))

    def test_loc_slicing(self, waltons_dataset):
        naf = NelsonAalenFitter().fit(waltons_dataset["T"])
        assert naf.cumulative_hazard_.loc[0:10].shape[0] == 4

    def test_iloc_slicing(self, waltons_dataset):
        naf = NelsonAalenFitter().fit(waltons_dataset["T"])
        assert naf.cumulative_hazard_.iloc[0:10].shape[0] == 10
        assert naf.cumulative_hazard_.iloc[0:-1].shape[0] == 32

    def test_smoothing_hazard_ties(self):
        T = np.random.binomial(20, 0.7, size=300)
        C = np.random.binomial(1, 0.8, size=300)
        naf = NelsonAalenFitter()
        naf.fit(T, C)
        naf.smoothed_hazard_(1.0)

    def test_smoothing_hazard_nontied(self):
        T = np.random.exponential(20, size=300) ** 2
        C = np.random.binomial(1, 0.8, size=300)
        naf = NelsonAalenFitter()
        naf.fit(T, C)
        naf.smoothed_hazard_(1.0)
        naf.fit(T)
        naf.smoothed_hazard_(1.0)

    def test_smoothing_hazard_ties_all_events_observed(self):
        T = np.random.binomial(20, 0.7, size=300)
        naf = NelsonAalenFitter()
        naf.fit(T)
        naf.smoothed_hazard_(1.0)

    def test_smoothing_hazard_with_spike_at_time_0(self):
        T = np.random.binomial(20, 0.7, size=300)
        T[np.random.binomial(1, 0.3, size=300).astype(bool)] = 0
        naf = NelsonAalenFitter()
        naf.fit(T)
        df = naf.smoothed_hazard_(bandwidth=0.1)
        assert df.iloc[0].values[0] > df.iloc[1].values[0]

    def test_nelson_aalen_smoothing(self):
        # this test was included because I was refactoring the estimators.
        np.random.seed(1)
        N = 10**4
        t = np.random.exponential(1, size=N)
        c = np.random.binomial(1, 0.9, size=N)
        naf = NelsonAalenFitter(nelson_aalen_smoothing=True)
        naf.fit(t, c)
        assert abs(naf.cumulative_hazard_["NA_estimate"].iloc[-1] - 8.545665) < 1e-6
        assert abs(naf.confidence_interval_["NA_estimate_upper_0.95"].iloc[-1] - 11.315662) < 1e-6
        assert abs(naf.confidence_interval_["NA_estimate_lower_0.95"].iloc[-1] - 6.4537448) < 1e-6

    def test_adding_weights_to_NelsonAalenFitter(self):
        n = 100
        df = pd.DataFrame()
        df["T"] = np.random.binomial(40, 0.5, n)
        df["E"] = np.random.binomial(1, 0.9, n)

        naf_no_weights = NelsonAalenFitter().fit(df["T"], df["E"])

        df_grouped = df.groupby(["T", "E"]).size().reset_index()
        naf_w_weights = NelsonAalenFitter().fit(df_grouped["T"], df_grouped["E"], weights=df_grouped[0])

        assert_frame_equal(naf_w_weights.cumulative_hazard_, naf_no_weights.cumulative_hazard_)


class TestBreslowFlemingHarringtonFitter:
    def test_BHF_fit_when_KMF_throws_an_error(self):
        bfh = BreslowFlemingHarringtonFitter()
        kmf = KaplanMeierFitter()

        observations = np.array(
            [1, 1, 2, 22, 30, 28, 32, 11, 14, 36, 31, 33, 33, 37, 35, 25, 31, 22, 26, 24, 35, 34, 30, 35, 40, 39, 2]
        )
        births = observations - 1

        with pytest.raises(StatError):
            kmf.fit(observations, entry=births)

        bfh.fit(observations, entry=births)


class TestParametricRegressionFitter:
    @pytest.fixture
    def rossi(self):
        rossi = load_rossi()
        rossi["Intercept"] = 1.0
        return rossi

    def test_AIC_on_models(self, rossi):
        model = WeibullAFTFitter(fit_intercept=False).fit(rossi, "week", "arrest")
        npt.assert_allclose(model.AIC_, -2 * model.log_likelihood_ + 2 * model.summary.shape[0])

    def test_fit_options(self, rossi):
        model = WeibullAFTFitter(fit_intercept=False).fit(rossi, "week", "arrest", fit_options={"tol": 0.1})
        npt.assert_allclose(model.AIC_, -2 * model.log_likelihood_ + 2 * model.summary.shape[0])

    def test_penalizer_can_be_an_array(self, rossi):

        wf_array = WeibullAFTFitter(penalizer=0.01 * np.ones(8), fit_intercept=False).fit(rossi, "week", "arrest")
        wf_float = WeibullAFTFitter(penalizer=0.01, fit_intercept=False).fit(rossi, "week", "arrest")

        assert_frame_equal(wf_array.summary, wf_float.summary)

    def test_penalizer_can_be_an_array_and_check_it_behaves_as_expected(self, rossi):

        penalty = np.array([0, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01])
        wf_array = WeibullAFTFitter(penalizer=penalty, fit_intercept=False).fit(rossi, "week", "arrest")
        wf_float = WeibullAFTFitter(penalizer=0.01, fit_intercept=False).fit(rossi, "week", "arrest")

        assert abs(wf_array.summary.loc[("lambda_", "age"), "coef"]) > abs(wf_float.summary.loc[("lambda_", "age"), "coef"])

    def test_custom_weibull_model_gives_the_same_data_as_implemented_weibull_model(self):
        from lifelines.utils.safe_exp import safe_exp

        class CustomWeibull(ParametricRegressionFitter):
            _scipy_fit_method = "SLSQP"
            _scipy_fit_options = {"ftol": 1e-10, "maxiter": 200}
            _fitted_parameter_names = ["lambda_", "rho_"]

            def _cumulative_hazard(self, params, T, Xs):
                lambda_ = safe_exp(anp.dot(Xs["lambda_"], params["lambda_"]))
                rho_ = safe_exp(anp.dot(Xs["rho_"], params["rho_"]))

                return (T / lambda_) ** rho_

            def _log_hazard(self, params, T, Xs):
                lambda_params = params["lambda_"]
                log_lambda_ = Xs["lambda_"] @ lambda_params

                rho_params = params["rho_"]
                log_rho_ = Xs["rho_"] @ rho_params

                return log_rho_ - log_lambda_ + anp.expm1(log_rho_) * (anp.log(T) - log_lambda_)

        cb = CustomWeibull(penalizer=0.0)
        wf = WeibullAFTFitter(penalizer=0.0)

        rossi = load_rossi()
        regressors = {"lambda_": "+".join(rossi.columns.difference(["week", "arrest"])), "rho_": "1"}

        cb.fit(rossi, "week", "arrest", regressors=regressors)
        wf.fit(rossi, "week", "arrest")

        assert_frame_equal(cb.summary.loc["lambda_"], wf.summary.loc["lambda_"], atol=0.1, check_like=True)
        npt.assert_allclose(cb.log_likelihood_, wf.log_likelihood_)

        cb.fit_left_censoring(rossi, "week", "arrest", regressors=regressors)
        wf.fit_left_censoring(rossi, "week", "arrest")

        assert_frame_equal(cb.summary.loc["lambda_"], wf.summary.loc["lambda_"], atol=0.1, check_like=True)
        npt.assert_allclose(cb.log_likelihood_, wf.log_likelihood_)

        rossi = rossi.loc[rossi["arrest"].astype(bool)]
        rossi["week_end"] = rossi["week"].copy()
        rossi = rossi.drop("arrest", axis=1)
        cb.fit_interval_censoring(rossi, "week", "week_end", regressors=regressors)
        wf.fit_interval_censoring(rossi, "week", "week_end")

        assert_frame_equal(cb.summary.loc["lambda_"], wf.summary.loc["lambda_"], atol=0.1, check_like=True)
        npt.assert_allclose(cb.log_likelihood_, wf.log_likelihood_, rtol=0.01)


class CureModelA(ParametricRegressionFitter):

    _fitted_parameter_names = ["lambda_", "beta_", "rho_"]

    def _cumulative_hazard(self, params, T, Xs):
        c = expit(anp.dot(Xs["beta_"], params["beta_"]))

        lambda_ = anp.exp(anp.dot(Xs["lambda_"], params["lambda_"]))
        rho_ = anp.exp(anp.dot(Xs["rho_"], params["rho_"]))
        cdf = 1 - anp.exp(-((T / lambda_) ** rho_))

        return -anp.log((1 - c) + c * (1 - cdf))


class CureModelB(ParametricRegressionFitter):
    # notice the c vs 1-c in the return statement
    _fitted_parameter_names = ["lambda_", "beta_", "rho_"]

    def _cumulative_hazard(self, params, T, Xs):
        c = expit(anp.dot(Xs["beta_"], params["beta_"]))

        lambda_ = anp.exp(anp.dot(Xs["lambda_"], params["lambda_"]))
        rho_ = anp.exp(anp.dot(Xs["rho_"], params["rho_"]))
        cdf = 1 - anp.exp(-((T / lambda_) ** rho_))

        return -anp.log(c + (1 - c) * (1 - cdf))


class CureModelC(CureModelB):
    # shuffle these parameter names - shouldn't change anything.
    _fitted_parameter_names = ["lambda_", "rho_", "beta_"]


class TestCustomRegressionModel:
    @pytest.fixture
    def rossi(self):
        rossi = load_rossi()
        return rossi

    def test_reparameterization_flips_the_sign(self, rossi):

        regressors = {"lambda_": rossi.columns.difference(["arrest", "week"]), "rho_": "1", "beta_": "fin + 1"}

        cmA = CureModelA()
        cmB = CureModelB()
        cmC = CureModelC()

        cmA.fit(rossi, "week", event_col="arrest", regressors=regressors)
        cmB.fit(rossi, "week", event_col="arrest", regressors=regressors)
        cmC.fit(
            rossi,
            "week",
            event_col="arrest",
            regressors={"lambda_": rossi.columns.difference(["week", "arrest"]), "rho_": "1", "beta_": "fin + 1"},
        )
        assert_frame_equal(cmA.summary.loc["lambda_"], cmB.summary.loc["lambda_"])
        assert_frame_equal(cmA.summary.loc["rho_"], cmB.summary.loc["rho_"])
        assert_frame_equal(cmC.summary, cmB.summary)
        assert_series_equal(cmA.params_.loc["beta_"], -cmB.params_.loc["beta_"])

    def test_custom_regression_model_accepts_infs_in_interval_censoring(self):
        df = load_c_botulinum_lag_phase()
        df.loc[df["lower_bound_days"] == 0, "lower_bound_days"] += 0.0001

        df["constant"] = 1.0

        regressors = {"lambda_": ["constant"], "mu_": ["NaCl_percent", "pH", "constant"], "sigma_": ["constant"]}
        gg = GeneralizedGammaRegressionFitter()
        gg.fit_interval_censoring(df, "lower_bound_days", "upper_bound_days", regressors=regressors)
        gg.print_summary()

    def test_warning_is_thrown_in_predict_median(self, rossi):

        rossi["constant"] = 1.0
        regressors = {"lambda_": ["constant"], "mu_": ["age", "fin", "constant"], "sigma_": ["constant"]}

        gg = GeneralizedGammaRegressionFitter()
        gg.fit(rossi, "week", "arrest", regressors=regressors)

        with pytest.warns(ApproximationWarning, match="Approximating"):
            gg.predict_median(rossi)

    def test_score_works_for_interval_censoring(self, rossi):
        df = load_c_botulinum_lag_phase()
        df.loc[df["lower_bound_days"] == 0, "lower_bound_days"] += 0.0001

        df["constant"] = 1.0

        regressors = {"lambda_": ["constant"], "mu_": ["NaCl_percent", "pH", "constant"], "sigma_": ["constant"]}
        gg = GeneralizedGammaRegressionFitter()
        gg.fit_interval_censoring(df, "lower_bound_days", "upper_bound_days", regressors=regressors)
        gg.score(df)

    def test_formulas_can_be_used_for_regressors(self, rossi):

        regressors = {"lambda_": "1", "mu_": "age + prio + paro + 1", "sigma_": "fin + age + 1"}
        gg = GeneralizedGammaRegressionFitter()
        gg.fit(rossi, "week", "arrest", regressors=regressors)
        assert gg.summary.shape[0] == 8


class TestRegressionFitters:
    @pytest.fixture
    def rossi(self):
        rossi = load_rossi()
        return rossi

    @pytest.fixture
    def regression_models_sans_strata_model(self):
        return [
            CoxPHFitter(penalizer=1e-6, baseline_estimation_method="breslow"),
            CoxPHFitter(penalizer=1e-6, baseline_estimation_method="piecewise", breakpoints=[15]),
            CoxPHFitter(penalizer=1e-6, baseline_estimation_method="spline", n_baseline_knots=2),
            CoxPHFitter(penalizer=1e-6, baseline_estimation_method="spline", n_baseline_knots=3),
            AalenAdditiveFitter(coef_penalizer=1.0, smoothing_penalizer=1.0),
            WeibullAFTFitter(fit_intercept=True),
            LogNormalAFTFitter(fit_intercept=True),
            LogLogisticAFTFitter(fit_intercept=True),
            PiecewiseExponentialRegressionFitter(breakpoints=[25.0]),
            CustomRegressionModelTesting(penalizer=1.0),
            GeneralizedGammaRegressionFitter(penalizer=5.0),
            # CRCSplineFitter(3),
        ]

    @pytest.fixture
    def regression_models(self, regression_models_sans_strata_model):
        regression_models_sans_strata_model.append(CoxPHFitter(strata=["wexp"]))
        regression_models_sans_strata_model.append(
            CoxPHFitter(strata=["wexp"], baseline_estimation_method="spline", n_baseline_knots=2)
        )
        regression_models_sans_strata_model.append(
            CoxPHFitter(strata=["wexp"], baseline_estimation_method="piecewise", breakpoints=[15])
        )
        return regression_models_sans_strata_model

    def test_no_observations(self, rossi, regression_models):
        rossi["arrest"] == 0
        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")
            fitter.print_summary()

    def test_compute_central_values_of_raw_training_data(self):

        central_values = RegressionFitter()._compute_central_values_of_raw_training_data

        empty_df = pd.DataFrame([])
        assert_frame_equal(central_values(empty_df), pd.DataFrame(index=["baseline"]))

        all_categorical = pd.DataFrame([{"var1": "A", "var2": "C"}, {"var1": "B", "var2": "C"}, {"var1": "B", "var2": "C"}])
        assert_frame_equal(central_values(all_categorical), pd.DataFrame([{"var1": "B", "var2": "C"}], index=["baseline"]))

        all_numeric = pd.DataFrame([{"var1": 0.4, "var2": -1}, {"var1": 0.5, "var2": -2}, {"var1": 0.6, "var2": -100}])
        assert_frame_equal(central_values(all_numeric), pd.DataFrame([{"var1": 0.5, "var2": -2}], index=["baseline"]))

        mix = pd.DataFrame([{"var1": "A", "var2": -1}, {"var1": "A", "var2": -2}, {"var1": "B", "var2": -100}])
        assert_frame_equal(central_values(mix), pd.DataFrame([{"var1": "A", "var2": -2}], index=["baseline"]))

    def test_compute_central_values_of_raw_training_data_with_strata(self):

        central_values = RegressionFitter()._compute_central_values_of_raw_training_data

        df = pd.DataFrame(
            [
                {"var1": 0.1, "var2": "D", "strata": "s1"},
                {"var1": 0.1, "var2": "C", "strata": "s2"},
                {"var1": 0.1, "var2": "D", "strata": "s1"},
                {"var1": 0.2, "var2": "C", "strata": "s2"},
            ]
        )
        assert_frame_equal(
            central_values(df, strata="strata"),
            pd.DataFrame([{"var1": 0.1, "var2": "D"}, {"var1": 0.15, "var2": "C"}], index=pd.Index(["s1", "s2"], name="strata")),
        )

    def test_compute_central_values_of_raw_training_data_with_integer_categorical_data(self):
        central_values = RegressionFitter()._compute_central_values_of_raw_training_data

        df = pd.DataFrame(
            [{"var1": 0.1, "cat1": 1}, {"var1": 0.1, "cat1": 2}, {"var1": 0.1, "cat1": 1}, {"var1": 0.2, "cat1": 3}]
        )
        df["cat1"] = pd.Categorical(df["cat1"])

        expected = pd.DataFrame(
            {
                "var1": pd.Series([0.1], dtype=float, index=["baseline"]),
                "cat1": pd.Series([1], dtype=pd.CategoricalDtype(categories=[1, 2, 3]), index=["baseline"]),
            }
        )

        assert_frame_equal(central_values(df), expected)

    def test_alpha_will_vary_the_statistics_in_summary(self, rossi):
        reg_005 = WeibullAFTFitter(alpha=0.05).fit(rossi, "week", "arrest")
        reg_010 = WeibullAFTFitter(alpha=0.10).fit(rossi, "week", "arrest")
        assert (
            reg_005.summary.loc[("lambda_", "fin"), "coef lower 95%"] < reg_010.summary.loc[("lambda_", "fin"), "coef lower 90%"]
        )

        reg_005 = CoxPHFitter(alpha=0.05).fit(rossi, "week", "arrest")
        reg_010 = CoxPHFitter(alpha=0.10).fit(rossi, "week", "arrest")
        assert reg_005.summary.loc["fin", "coef lower 95%"] < reg_010.summary.loc["fin", "coef lower 90%"]

        reg_005 = CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=2, alpha=0.05).fit(rossi, "week", "arrest")
        reg_010 = CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=2, alpha=0.10).fit(rossi, "week", "arrest")
        assert reg_005.summary.loc[("beta_", "fin"), "coef lower 95%"] < reg_010.summary.loc[("beta_", "fin"), "coef lower 90%"]

    def test_spline_model_can_use_score(self, rossi):
        cph_spline = CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=2)
        cph_spline.fit(rossi, "week", "arrest")
        cph_spline.score(rossi, scoring_method="log_likelihood")

    def test_score_method_returns_same_value_for_unpenalized_models(self, rossi):
        regression_models = [CoxPHFitter(), WeibullAFTFitter()]
        for fitter in regression_models:

            fitter.fit(rossi, "week", "arrest")
            npt.assert_almost_equal(fitter.score(rossi, scoring_method="log_likelihood"), fitter.log_likelihood_ / rossi.shape[0])
            npt.assert_almost_equal(fitter.score(rossi, scoring_method="concordance_index"), fitter.concordance_index_)

        regression_models = [PiecewiseExponentialRegressionFitter(breakpoints=[25.0])]
        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")
            npt.assert_almost_equal(fitter.score(rossi, scoring_method="log_likelihood"), fitter.log_likelihood_ / rossi.shape[0])
            npt.assert_almost_equal(fitter.score(rossi, scoring_method="concordance_index"), fitter.concordance_index_)

    @pytest.mark.xfail
    def test_score_method_returns_same_value_for_unpenalized_models_fails_for_an_unknown_reason(self, rossi):

        regression_models = [CustomRegressionModelTesting()]
        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")
            npt.assert_almost_equal(fitter.score(rossi, scoring_method="log_likelihood"), fitter.log_likelihood_ / rossi.shape[0])
            npt.assert_almost_equal(fitter.score(rossi, scoring_method="concordance_index"), fitter.concordance_index_)

    def test_print_summary(self, rossi, regression_models):
        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")
            fitter.print_summary(columns=["p", "coef", "std(coef)"], decimals=3)

    def test_all_models_have_regressors_property(self, rossi, regression_models):
        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")
            assert hasattr(fitter, "regressors")

    def test_pickle_serialization(self, rossi, regression_models):
        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")

            unpickled = pickle.loads(pickle.dumps(fitter))
            dif = (fitter.durations - unpickled.durations).sum()
            assert dif == 0

    def test_dill_serialization(self, rossi, regression_models):
        from dill import dumps, loads

        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")

            unpickled = loads(dumps(fitter))
            dif = (fitter.durations - unpickled.durations).sum()
            assert dif == 0

    def test_joblib_serialization(self, rossi, regression_models):
        from joblib import dump, load

        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")

            dump(fitter, "filename.joblib")
            unpickled = load("filename.joblib")
            dif = (fitter.durations - unpickled.durations).sum()
            assert dif == 0

    def test_fit_will_accept_object_dtype_as_event_col(self, regression_models_sans_strata_model, rossi):
        # issue #638
        rossi["arrest"] = rossi["arrest"].astype(object)
        rossi["arrest"].iloc[0] = None

        assert rossi["arrest"].dtype == object
        rossi = rossi.dropna()
        assert rossi["arrest"].dtype == object

        for fitter in regression_models_sans_strata_model:
            fitter.fit(rossi, "week", "arrest")

    def test_fit_raise_an_error_if_nan_in_event_col(self, regression_models_sans_strata_model):
        df = pd.DataFrame({"T": np.arange(1, 11), "E": [True] * 9 + [None]})

        for fitter in regression_models_sans_strata_model:
            with pytest.raises(TypeError, match="NaNs were detected in the dataset"):
                fitter.fit(df, "T", "E")

    def test_predict_methods_in_regression_return_same_types(self, regression_models, rossi):

        fitted_regression_models = list(
            map(lambda model: model.fit(rossi, duration_col="week", event_col="arrest"), regression_models)
        )

        for predict_method in [
            "predict_percentile",
            "predict_median",
            "predict_expectation",
            "predict_survival_function",
            "predict_cumulative_hazard",
        ]:
            for fitter1, fitter2 in combinations(fitted_regression_models, 2):
                assert isinstance(getattr(fitter1, predict_method)(rossi), type(getattr(fitter2, predict_method)(rossi)))

    def test_predict_methods_in_regression_return_same_index(self, regression_models, rossi):

        fitted_regression_models = list(
            map(lambda model: model.fit(rossi, duration_col="week", event_col="arrest"), regression_models)
        )

        X = rossi.loc[:10]

        for predict_method in [
            "predict_percentile",
            "predict_median",
            "predict_expectation",
            "predict_survival_function",
            "predict_cumulative_hazard",
        ]:
            for fitter1, fitter2 in combinations(fitted_regression_models, 2):
                assert_index_equal(getattr(fitter1, predict_method)(X).index, getattr(fitter2, predict_method)(X).index)

    def test_duration_vector_can_be_normalized_up_to_an_intercept(self, regression_models, rossi):
        t = rossi["week"]
        normalized_rossi = rossi.copy()
        normalized_rossi["week"] = (normalized_rossi["week"]) / t.std()

        for fitter in [CoxPHFitter(penalizer=1e-6, baseline_estimation_method="spline", n_baseline_knots=3)]:
            if (
                isinstance(fitter, PiecewiseExponentialRegressionFitter)
                or isinstance(fitter, CustomRegressionModelTesting)
                or isinstance(fitter, GeneralizedGammaRegressionFitter)
            ):
                continue

            # we drop indexes since aaf will have a different "time" index.
            hazards = fitter.fit(rossi, duration_col="week", event_col="arrest").params_
            hazards_norm = fitter.fit(normalized_rossi, duration_col="week", event_col="arrest").params_

            if isinstance(hazards, pd.DataFrame):
                assert_frame_equal(hazards.reset_index(drop=True), hazards_norm.reset_index(drop=True))
            else:
                if isinstance(hazards.index, pd.MultiIndex):
                    assert_series_equal(
                        hazards.drop("Intercept", axis=0, level=1),
                        hazards_norm.drop("Intercept", axis=0, level=1),
                        atol=0.1,
                    )
                else:
                    assert_series_equal(hazards, hazards_norm, atol=0.1)

    def test_prediction_methods_respect_index(self, regression_models, rossi):
        X = rossi.iloc[:4].sort_index(ascending=False)
        expected_index = pd.Index(np.array([3, 2, 1, 0]))

        for fitter in regression_models:
            fitter.fit(rossi, duration_col="week", event_col="arrest")
            npt.assert_array_equal(fitter.predict_percentile(X).index, expected_index)
            npt.assert_array_equal(fitter.predict_expectation(X).index, expected_index)
            try:
                npt.assert_array_equal(fitter.predict_partial_hazard(X).index, expected_index)
            except AttributeError:
                pass

    def test_error_is_not_raised_if_using_non_numeric_data_in_fit(self):
        df = pd.DataFrame.from_dict(
            {
                "t": [1.0, 5.0, 3.0, 4.0],
                "bool_": [True, False, False, True],
                "int_": [1, -1, 0, 2],
                "uint8_": pd.Series([1, 0, 2, 1], dtype="uint8"),
                "string_": ["2.5", "2.5", "a", "a"],
                "float_": [1.2, -0.5, 0.0, 2.2],
                "categorya_": pd.Series([1, 2, 2, 1], dtype="category"),
                "categoryb_": pd.Series(["a", "a", "b", "b"], dtype="category"),
            }
        )

        for fitter in [CoxPHFitter(), WeibullAFTFitter()]:
            for subset in [["t", "categoryb_"], ["t", "string_"], ["t", "uint8_"], ["t", "categorya_"], ["t", "bool_"]]:
                formula = "%s" % subset[-1]
                fitter.fit(df[subset], duration_col="t", formula=formula)

    @pytest.mark.xfail
    def test_regression_model_has_concordance_index_(self, regression_models, rossi):

        for fitter in regression_models:
            assert not hasattr(fitter, "concordance_index_")
            fitter.fit(rossi, duration_col="week", event_col="arrest")
            assert hasattr(fitter, "concordance_index_")

    @pytest.mark.xfail
    def test_regression_model_updates_concordance_index_(self, regression_models, rossi):

        for fitter in regression_models:
            assert not hasattr(fitter, "concordance_index_")
            fitter.fit(rossi, duration_col="week", event_col="arrest")
            assert hasattr(fitter, "concordance_index_")
            first_score_ = fitter.concordance_index_

            fitter.fit(rossi.head(50), duration_col="week", event_col="arrest")
            assert first_score_ != fitter.concordance_index_

    def test_error_is_thrown_if_there_is_nans_in_the_duration_col(self, regression_models, rossi):
        rossi.loc[3, "week"] = None
        for fitter in regression_models:
            with pytest.raises(TypeError):
                fitter.fit(rossi, "week", "arrest")

    def test_error_is_thrown_if_there_is_nans_in_the_event_col(self, regression_models, rossi):
        rossi.loc[3, "arrest"] = None
        for fitter in regression_models:
            with pytest.raises(TypeError):
                fitter.fit(rossi, "week", "arrest")

    def test_all_models_have_censoring_type(self, regression_models, rossi):
        for fitter in regression_models:
            fitter.fit(rossi, "week", "arrest")
            assert hasattr(fitter, "_censoring_type")

    def test_regression_models_will_not_fail_when_provided_int_times_on_prediction(
        self, regression_models_sans_strata_model, rossi
    ):
        # reported an issue
        for fitter in regression_models_sans_strata_model:
            df = rossi.copy()

            fitter.fit(df, duration_col="week", event_col="arrest")

            # select only censored items
            df = df[df["arrest"] == 0]

            func = lambda row: fitter.predict_survival_function(row, times=row["week"])
            df.apply(func, axis=1)

        assert True


class TestPiecewiseExponentialRegressionFitter:
    def test_print_summary(self):
        df = load_rossi()
        pew = PiecewiseExponentialRegressionFitter(breakpoints=[25, 40]).fit(df, "week", "arrest")
        pew.print_summary()

    def test_inference(self):

        N, d = 80000, 2

        # some numbers take from http://statwonk.com/parametric-survival.html
        breakpoints = (1, 31, 34, 62, 65)

        betas = np.array(
            [
                [1.0, -0.2, np.log(15)],
                [5.0, -0.4, np.log(333)],
                [9.0, -0.6, np.log(18)],
                [5.0, -0.8, np.log(500)],
                [2.0, -1.0, np.log(20)],
                [1.0, -1.2, np.log(500)],
            ]
        )

        X = 0.1 * np.random.exponential(size=(N, d))
        X = np.c_[X, np.ones(N)]

        T = np.empty(N)
        for i in range(N):
            lambdas = np.exp(-betas.dot(X[i, :]))
            T[i] = piecewise_exponential_survival_data(1, breakpoints, lambdas)[0]

        T_censor = np.minimum(T.mean() * np.random.exponential(size=N), 110)  # 110 is the end of observation, eg. current time.

        df = pd.DataFrame(X[:, :-1], columns=["var1", "var2"])

        df["T"] = np.round(np.maximum(np.minimum(T, T_censor), 0.1), 1)
        df["E"] = T <= T_censor

        pew = PiecewiseExponentialRegressionFitter(breakpoints=breakpoints, penalizer=0.00001).fit(df, "T", "E")

        def assert_allclose(variable_name_tuple, actual):
            npt.assert_allclose(
                pew.summary.loc[variable_name_tuple, "coef"],
                actual,
                rtol=1,
                atol=2 * pew.summary.loc[variable_name_tuple, "se(coef)"],
            )

        assert_allclose(("lambda_0_", "var1"), betas[0][0])
        assert_allclose(("lambda_0_", "var2"), betas[0][1])
        assert_allclose(("lambda_0_", "Intercept"), betas[0][2])

        assert_allclose(("lambda_1_", "var1"), betas[1][0])
        assert_allclose(("lambda_1_", "var2"), betas[1][1])
        assert_allclose(("lambda_1_", "Intercept"), betas[1][2])

        assert_allclose(("lambda_5_", "var1"), betas[-1][0])
        assert_allclose(("lambda_5_", "var2"), betas[-1][1])
        assert_allclose(("lambda_5_", "Intercept"), betas[-1][2])


class TestAFTFitters:
    @pytest.fixture
    def models(self):
        return [WeibullAFTFitter(), LogNormalAFTFitter(), LogLogisticAFTFitter()]

    def test_predict_median_takes_dataframe_with_bools(self):

        df = pd.DataFrame(
            [
                {"dep_y_obs": 1.0, "dep_y_cens": False, "idp_x1_obs": 5.0, "idp_x1_cens": True},
                {"dep_y_obs": 3.0, "dep_y_cens": True, "idp_x1_obs": 3.0, "idp_x1_cens": False},
                {"dep_y_obs": 2.0, "dep_y_cens": True, "idp_x1_obs": 2.0, "idp_x1_cens": False},
                {"dep_y_obs": 2.0, "dep_y_cens": False, "idp_x1_obs": 6.0, "idp_x1_cens": True},
                {"dep_y_obs": 2.5, "dep_y_cens": True, "idp_x1_obs": 7.0, "idp_x1_cens": True},
                {"dep_y_obs": 2.7, "dep_y_cens": True, "idp_x1_obs": 8.0, "idp_x1_cens": True},
            ]
        )

        wf = WeibullAFTFitter()
        wf.fit_left_censoring(df, "dep_y_obs", "dep_y_cens")
        wf.predict_median(df)

    def test_predict_median_accepts_series(self, rossi):
        df = pd.DataFrame(
            [
                {"dep_y_obs": 1.0, "dep_y_cens": False, "idp_x1_obs": 5.0, "idp_x1_cens": True},
                {"dep_y_obs": 3.0, "dep_y_cens": True, "idp_x1_obs": 3.0, "idp_x1_cens": False},
                {"dep_y_obs": 2.0, "dep_y_cens": True, "idp_x1_obs": 2.0, "idp_x1_cens": False},
                {"dep_y_obs": 2.0, "dep_y_cens": False, "idp_x1_obs": 6.0, "idp_x1_cens": True},
                {"dep_y_obs": 2.5, "dep_y_cens": True, "idp_x1_obs": 7.0, "idp_x1_cens": True},
                {"dep_y_obs": 2.7, "dep_y_cens": True, "idp_x1_obs": 8.0, "idp_x1_cens": True},
            ]
        )

        wf = WeibullAFTFitter()
        wf.fit_left_censoring(df, "dep_y_obs", "dep_y_cens")
        wf.predict_median(df.loc[1])

    def test_heterogenous_initial_point(self, rossi):
        aft = WeibullAFTFitter()
        aft.fit(rossi, "week", "arrest", initial_point={"lambda_": np.zeros(8), "rho_": np.zeros(1)})
        with pytest.raises(ValueError):
            aft.fit(rossi, "week", "arrest", initial_point={"lambda_": np.zeros(7), "rho_": np.zeros(1)})

        aft.fit(rossi, "week", "arrest", initial_point=np.zeros(9))
        with pytest.raises(ValueError):
            aft.fit(rossi, "week", "arrest", initial_point=np.zeros(10))

    def test_percentile_gives_proper_result_compared_to_survival_function(self, rossi, models):
        for model in models:
            model.fit(rossi, "week", "arrest")
            times = np.linspace(1, 2000, 5000)
            p = 0.1
            subject = rossi.loc[[400]]
            assert (
                abs(
                    model.predict_percentile(subject, p=p)
                    - qth_survival_time(p, model.predict_survival_function(subject, times=times))
                ).loc[400]
                < 0.5
            )
            assert (
                abs(
                    model.predict_percentile(subject, p=p, conditional_after=[50])
                    - qth_survival_time(p, model.predict_survival_function(subject, times=times, conditional_after=[50]))
                ).loc[400]
                < 0.5
            )

    def test_fit_intercept_can_be_false_and_not_provided(self, rossi):
        # nonsensical data
        interval_rossi = rossi.copy()
        interval_rossi["start"] = 0
        interval_rossi["end"] = rossi["week"]
        interval_rossi["arrest"] = False
        interval_rossi = interval_rossi.drop("week", axis=1)

        # nonsensical data
        left_rossi = rossi.copy()
        left_rossi["week"] = 1 / rossi["week"] + 1

        for fitter in [WeibullAFTFitter(fit_intercept=False)]:
            fitter.fit_right_censoring(rossi, "week", "arrest")
            fitter.fit_left_censoring(left_rossi, "week", "arrest")
            fitter.fit_interval_censoring(interval_rossi, "start", "end", "arrest")

    def test_fit_intercept_can_be_false_but_provided(self, rossi):
        rossi["intercept"] = 1.0
        for fitter in [
            WeibullAFTFitter(fit_intercept=False),
            LogNormalAFTFitter(fit_intercept=False),
            LogLogisticAFTFitter(fit_intercept=False),
        ]:
            fitter.fit(rossi, "week", "arrest", ancillary=rossi[["intercept"]])

    def test_warning_is_present_if_entry_greater_than_duration(self, rossi, models):
        rossi["start"] = 10
        for fitter in models:
            with pytest.raises(ValueError, match="entry >= duration"):
                fitter.fit(rossi, "week", "arrest", entry_col="start")

    def test_weights_col_and_start_col_is_not_included_in_the_output(self, models, rossi):
        rossi["weights"] = 2.0
        rossi["start"] = 0.0

        for fitter in models:
            fitter.fit(rossi, "week", "arrest", weights_col="weights", entry_col="start", ancillary=False)
            assert "weights" not in fitter.params_.index.get_level_values(1)
            assert "start" not in fitter.params_.index.get_level_values(1)

            fitter.fit(rossi, "week", "arrest", weights_col="weights", entry_col="start", ancillary=True)
            assert "weights" not in fitter.params_.index.get_level_values(1)
            assert "start" not in fitter.params_.index.get_level_values(1)

            fitter.fit(rossi, "week", "arrest", weights_col="weights", entry_col="start", ancillary=rossi)
            assert "weights" not in fitter.params_.index.get_level_values(1)
            assert "start" not in fitter.params_.index.get_level_values(1)

    def test_accept_initial_params(self, rossi, models):
        for fitter in models:
            fitter.fit(rossi, "week", "arrest", initial_point=0.01 * np.ones(9))

    def test_log_likelihood_is_maximized_for_data_generating_model(self):

        N = 50000
        p = 0.5
        bX = np.log(0.5)
        bZ = np.log(4)

        Z = np.random.binomial(1, p, size=N)
        X = np.random.binomial(1, 0.5, size=N)

        # weibullAFT should have the best fit -> largest ll
        W = weibull_min.rvs(1, scale=1, loc=0, size=N)

        Y = bX * X + bZ * Z + np.log(W)
        T = np.exp(Y)

        df = pd.DataFrame({"T": T, "x": X, "z": Z})

        wf = WeibullAFTFitter().fit(df, "T")
        lnf = LogNormalAFTFitter().fit(df, "T")
        llf = LogLogisticAFTFitter().fit(df, "T")

        assert wf.log_likelihood_ > lnf.log_likelihood_
        assert wf.log_likelihood_ > llf.log_likelihood_

        # lognormal should have the best fit -> largest ll
        W = norm.rvs(scale=1, loc=0, size=N)

        Y = bX * X + bZ * Z + W
        T = np.exp(Y)

        df = pd.DataFrame({"T": T, "x": X, "z": Z})

        wf = WeibullAFTFitter().fit(df, "T")
        lnf = LogNormalAFTFitter().fit(df, "T")
        llf = LogLogisticAFTFitter().fit(df, "T")

        assert lnf.log_likelihood_ > wf.log_likelihood_
        assert lnf.log_likelihood_ > llf.log_likelihood_

        # loglogistic should have the best fit -> largest ll
        W = logistic.rvs(scale=1, loc=0, size=N)

        Y = bX * X + bZ * Z + W
        T = np.exp(Y)

        df = pd.DataFrame({"T": T, "x": X, "z": Z})

        wf = WeibullAFTFitter().fit(df, "T")
        lnf = LogNormalAFTFitter().fit(df, "T")
        llf = LogLogisticAFTFitter().fit(df, "T")

        assert llf.log_likelihood_ > wf.log_likelihood_
        assert llf.log_likelihood_ > lnf.log_likelihood_

    def test_aft_median_behaviour(self, models, rossi):
        for aft in models:
            aft.fit(rossi, "week", "arrest")

            subject = aft._central_values

            baseline_survival = aft.predict_median(subject).squeeze()

            subject.loc["baseline", "prio"] += 1
            accelerated_survival = aft.predict_median(subject).squeeze()
            factor = aft.summary.loc[(aft._primary_parameter_name, "prio"), "exp(coef)"]
            npt.assert_allclose(accelerated_survival, baseline_survival * factor)

    def test_aft_mean_behaviour(self, models, rossi):
        for aft in models:
            aft.fit(rossi, "week", "arrest")

            subject = aft._central_values

            baseline_survival = aft.predict_expectation(subject).squeeze()

            subject.loc["baseline", "prio"] += 1
            accelerated_survival = aft.predict_expectation(subject).squeeze()
            factor = aft.summary.loc[(aft._primary_parameter_name, "prio"), "exp(coef)"]
            npt.assert_allclose(accelerated_survival, baseline_survival * factor)

    def test_aft_models_can_do_left_censoring(self, models):
        N = 100
        T_actual = 0.5 * np.random.weibull(5, size=N)

        MIN_0 = np.percentile(T_actual, 5)
        MIN_1 = np.percentile(T_actual, 10)
        MIN_2 = np.percentile(T_actual, 30)
        MIN_3 = np.percentile(T_actual, 50)

        T = T_actual.copy()
        ix = np.random.randint(4, size=N)

        T = np.where(ix == 0, np.maximum(T, MIN_0), T)
        T = np.where(ix == 1, np.maximum(T, MIN_1), T)
        T = np.where(ix == 2, np.maximum(T, MIN_2), T)
        T = np.where(ix == 3, np.maximum(T, MIN_3), T)
        E = T_actual == T
        df = pd.DataFrame({"T": T, "E": E})

        for model in models:
            model.fit_left_censoring(df, "T", "E")
            model.print_summary()

    def test_model_ancillary_parameter_works_as_expected(self, rossi):
        aft = WeibullAFTFitter(model_ancillary=True)
        aft.fit(rossi, "week", "arrest")
        assert aft.summary.loc["rho_"].shape[0] == 8

        assert aft.predict_median(rossi).shape[0] == rossi.shape[0]


class TestLogNormalAFTFitter:
    @pytest.fixture
    def aft(self):
        return LogNormalAFTFitter()

    def test_coefs_with_fitted_ancillary_params(self, aft, rossi):
        """
        library('flexsurv')
        r = flexsurvreg(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio + sdlog(prio) + sdlog(age), data=df, dist='lnorm')
        r$coef
        """
        aft.fit(rossi, "week", "arrest", ancillary=rossi[["prio", "age"]])

        npt.assert_allclose(aft.summary.loc[("mu_", "paro"), "coef"], 0.09698076, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("mu_", "prio"), "coef"], -0.10216665, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("mu_", "Intercept"), "coef"], 2.63459946, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("sigma_", "Intercept"), "coef"], -0.47257736, rtol=1e-1)
        npt.assert_allclose(aft.summary.loc[("sigma_", "prio"), "coef"], -0.04741327, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("sigma_", "age"), "coef"], 0.03769193, rtol=1e-1)


class TestLogLogisticAFTFitter:
    @pytest.fixture
    def aft(self):
        return LogLogisticAFTFitter()

    def test_coefs_with_fitted_ancillary_params(self, aft, rossi):
        """
        library('flexsurv')
        r = flexsurvreg(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio + shape(prio) + shape(age), data=df, dist='llogis')
        r$coef
        """
        aft.fit(rossi, "week", "arrest", ancillary=rossi[["prio", "age"]])

        npt.assert_allclose(aft.summary.loc[("alpha_", "paro"), "coef"], 0.07512732, rtol=1e-1)
        npt.assert_allclose(aft.summary.loc[("alpha_", "prio"), "coef"], -0.08837948, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("alpha_", "Intercept"), "coef"], 2.75013722, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("beta_", "Intercept"), "coef"], 1.22928200, rtol=1e-1)
        npt.assert_allclose(aft.summary.loc[("beta_", "prio"), "coef"], 0.02707661, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("beta_", "age"), "coef"], -0.03853006, rtol=1e-1)

    def test_proportional_odds(self, aft, rossi):

        aft.fit(rossi, "week", "arrest")

        subject = aft._central_values

        baseline_survival = aft.predict_survival_function(subject).squeeze()

        subject.loc["baseline", "prio"] += 1
        accelerated_survival = aft.predict_survival_function(subject).squeeze()

        factor = aft.summary.loc[("alpha_", "prio"), "exp(coef)"]
        expon = aft.summary.loc[("beta_", "Intercept"), "exp(coef)"]
        npt.assert_allclose(
            baseline_survival / (1 - baseline_survival) * factor**expon, accelerated_survival / (1 - accelerated_survival)
        )


class TestWeibullAFTFitter:
    @pytest.fixture
    def aft(self):
        return WeibullAFTFitter()

    def test_interval_censoring_with_formula(self, aft):
        df = load_diabetes()
        df["gender"] = df["gender"] == "male"
        df["E"] = df["left"] == df["right"]
        df["gender"] = df["gender"].astype(int)

        aft.fit_interval_censoring(df, "left", "right", "E", formula="gender")

    def test_fitted_coefs_with_eha_when_left_truncated(self, aft, rossi):
        """
        library(eha)
        df = read.csv("~/code/lifelines/lifelines/datasets/rossi.csv")
        df['start'] = 0
        df[df['week'] > 10, 'start'] = 2
        r = aftreg(Surv(start, week, arrest) ~ fin + race + wexp + mar + paro + prio + age, data=df)
        summary(r)
        """

        rossi["start"] = 0
        rossi.loc[rossi["week"] > 10, "start"] = 2

        aft.fit(rossi, "week", "arrest", entry_col="start")

        # it's the negative in EHA
        npt.assert_allclose(aft.summary.loc[("lambda_", "fin"), "coef"], 0.28865175, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "age"), "coef"], 0.04323855, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "race"), "coef"], -0.23883560, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "wexp"), "coef"], 0.11339258, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "mar"), "coef"], 0.33081212, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "paro"), "coef"], 0.06303764, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "prio"), "coef"], -0.06954257, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "coef"], 3.98650094, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "coef"], 0.27564733, rtol=1e-4)

    def test_fitted_se_with_eha_when_left_truncated(self, aft, rossi):
        """
        library(eha)
        df = read.csv("~/code/lifelines/lifelines/datasets/rossi.csv")
        df['start'] = 0
        df[df['week'] > 10, 'start'] = 2
        r = aftreg(Surv(start, week, arrest) ~ fin + race + wexp + mar + paro + prio + age, data=df)
        summary(r)
        """

        rossi["start"] = 0
        rossi.loc[rossi["week"] > 10, "start"] = 2

        aft.fit(rossi, "week", "arrest", entry_col="start")

        npt.assert_allclose(aft.summary.loc[("lambda_", "fin"), "se(coef)"], 0.148, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "age"), "se(coef)"], 0.017, rtol=1e-1)
        npt.assert_allclose(aft.summary.loc[("lambda_", "race"), "se(coef)"], 0.235, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "wexp"), "se(coef)"], 0.162, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "mar"), "se(coef)"], 0.292, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "paro"), "se(coef)"], 0.149, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "prio"), "se(coef)"], 0.022, rtol=1e-1)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "se(coef)"], 0.446, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "se(coef)"], 0.104, rtol=1e-2)

    def test_fitted_coefs_match_with_flexsurv_has(self, aft, rossi):
        """
        library('flexsurv')
        df = read.csv("~/code/lifelines/lifelines/datasets/rossi.csv")
        r = flexsurvreg(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio, data=df, dist='weibull')
        r$coef
        """
        aft.fit(rossi, "week", "arrest")

        npt.assert_allclose(aft.summary.loc[("lambda_", "fin"), "coef"], 0.27230591, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "age"), "coef"], 0.04072758, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "race"), "coef"], -0.22480808, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "wexp"), "coef"], 0.10664712, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "mar"), "coef"], 0.31095531, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "paro"), "coef"], 0.05883352, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "prio"), "coef"], -0.06580211, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "coef"], 3.98968559, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "coef"], 0.33911900, rtol=1e-4)

    def test_fitted_se_match_with_flexsurv_has(self, aft, rossi):
        """
        library('flexsurv')
        r = flexsurvreg(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio, data=df, dist='weibull')
        diag(sqrt(vcov(r)))
        """
        aft.fit(rossi, "week", "arrest")

        npt.assert_allclose(aft.summary.loc[("lambda_", "fin"), "se(coef)"], 0.13796834, rtol=1e-4)
        npt.assert_allclose(aft.summary.loc[("lambda_", "age"), "se(coef)"], 0.01599442, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "race"), "se(coef)"], 0.22015347, rtol=1e-4)
        npt.assert_allclose(aft.summary.loc[("lambda_", "wexp"), "se(coef)"], 0.15154541, rtol=1e-4)
        npt.assert_allclose(aft.summary.loc[("lambda_", "mar"), "se(coef)"], 0.27326405, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "paro"), "se(coef)"], 0.13963680, rtol=1e-4)
        npt.assert_allclose(aft.summary.loc[("lambda_", "prio"), "se(coef)"], 0.02093981, rtol=1e-4)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "se(coef)"], 0.41904636, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "se(coef)"], 0.08900064, rtol=1e-3)

    def test_fitted_log_likelihood_match_with_flexsurv_has(self, aft, rossi):
        # survreg(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio, data=df, dist='weibull')
        aft.fit(rossi, "week", "arrest")
        npt.assert_allclose(aft.log_likelihood_, -679.9166)

    def test_fitted_log_likelihood_ratio_test_match_with_flexsurv_has(self, aft, rossi):
        # survreg(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio, data=df, dist='weibull')
        aft.fit(rossi, "week", "arrest")
        npt.assert_allclose(aft.log_likelihood_ratio_test().test_statistic, 33.42, rtol=0.01)

    def test_coefs_with_fitted_ancillary_params(self, aft, rossi):
        """
        library('flexsurv')
        r = flexsurvreg(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio + shape(prio) + shape(age), data=df, dist='weibull')
        r$coef
        """
        aft.fit(rossi, "week", "arrest", ancillary=rossi[["prio", "age"]])

        npt.assert_allclose(aft.summary.loc[("lambda_", "paro"), "coef"], 0.088364095, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "prio"), "coef"], -0.074052141, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "coef"], 2.756355922, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "coef"], 1.163429253, rtol=1e-4)
        npt.assert_allclose(aft.summary.loc[("rho_", "prio"), "coef"], 0.008982523, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("rho_", "age"), "coef"], -0.037069994, rtol=1e-4)

    def test_ancillary_True_is_same_as_full_df(self, rossi):

        aft1 = WeibullAFTFitter().fit(rossi, "week", "arrest", ancillary=True)
        aft2 = WeibullAFTFitter().fit(rossi, "week", "arrest", ancillary=rossi)

        assert_frame_equal(aft1.summary, aft2.summary, check_like=True)

    def test_ancillary_None_is_same_as_False(self, rossi):

        aft1 = WeibullAFTFitter().fit(rossi, "week", "arrest", ancillary=None)
        aft2 = WeibullAFTFitter().fit(rossi, "week", "arrest", ancillary=False)

        assert_frame_equal(aft1.summary, aft2.summary)

    def test_fit_intercept(self, rossi):
        aft_without_intercept = WeibullAFTFitter(fit_intercept=True)
        aft_without_intercept.fit(rossi, "week", "arrest", ancillary=rossi)

        rossi["Intercept"] = 1.0
        aft_with_intercept = WeibullAFTFitter(fit_intercept=False)
        aft_with_intercept.fit(rossi, "week", "arrest", ancillary=rossi)

        assert_frame_equal(aft_with_intercept.summary.sort_index(), aft_without_intercept.summary.sort_index())

    def test_passing_in_additional_ancillary_in_predict_methods_if_fitted_with_one(self, rossi):

        aft = WeibullAFTFitter().fit(rossi, "week", "arrest", ancillary=True)
        aft.predict_median(rossi, ancillary=rossi)
        aft.predict_percentile(rossi, ancillary=rossi)
        aft.predict_cumulative_hazard(rossi, ancillary=rossi)
        aft.predict_hazard(rossi, ancillary=rossi)
        aft.predict_survival_function(rossi, ancillary=rossi)

        aft.predict_median(rossi)
        aft.predict_percentile(rossi)
        aft.predict_cumulative_hazard(rossi)
        aft.predict_hazard(rossi)
        aft.predict_survival_function(rossi)

    def test_passing_in_additional_ancillary_in_predict_methods_okay_if_not_fitted_with_one(self, rossi, aft):

        aft.fit(rossi, "week", "arrest", ancillary=False)
        aft.predict_median(rossi, ancillary=rossi)
        aft.predict_percentile(rossi, ancillary=rossi)
        aft.predict_hazard(rossi, ancillary=rossi)
        aft.predict_survival_function(rossi, ancillary=rossi)

    def test_robust_errors_against_R(self, rossi, aft):
        # r = survreg(Surv(week, arrest) ~ fin + race + wexp + mar + paro + prio + age, data=df, dist='weibull', robust=TRUE)

        aft.fit(rossi, "week", "arrest", robust=True)

        npt.assert_allclose(aft.summary.loc[("lambda_", "fin"), "se(coef)"], 0.1423, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "age"), "se(coef)"], 0.0174, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "race"), "se(coef)"], 0.2107, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "wexp"), "se(coef)"], 0.1577, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "mar"), "se(coef)"], 0.2748, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "paro"), "se(coef)"], 0.1429, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "prio"), "se(coef)"], 0.0208, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "se(coef)"], 0.4631, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "se(coef)"], 0.0870, rtol=1e-3)

    def test_robust_errors_against_R_with_weights(self, rossi, aft):
        # r = survreg(Surv(week, arrest) ~ fin + race + wexp + mar + paro + prio, data=df, dist='weibull', robust=TRUE, weights=age)

        aft.fit(rossi, "week", "arrest", robust=True, weights_col="age")

        npt.assert_allclose(aft.summary.loc[("lambda_", "fin"), "se(coef)"], 0.006581, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "race"), "se(coef)"], 0.010367, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "wexp"), "se(coef)"], 0.007106, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "mar"), "se(coef)"], 0.012179, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "paro"), "se(coef)"], 0.006427, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "prio"), "se(coef)"], 0.000964, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "se(coef)"], 0.013165, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "se(coef)"], 0.003630, rtol=1e-3)

    def test_inference_is_the_same_if_using_right_censorship_or_interval_censorship_with_inf_endpoints(self, rossi, aft):
        df = rossi.copy()
        df["start"] = df["week"]
        df["stop"] = np.where(df["arrest"], df["start"], np.inf)
        df = df.drop("week", axis=1)

        aft.fit_interval_censoring(df, lower_bound_col="start", upper_bound_col="stop", event_col="arrest")
        interval_censored_results = aft.summary.copy()

        aft.fit_right_censoring(rossi, "week", event_col="arrest")
        right_censored_results = aft.summary.copy()

        assert_frame_equal(interval_censored_results, right_censored_results, atol=0.01)

    def test_weibull_interval_censoring_inference_on_known_R_output(self, aft):
        """
        library(flexsurv)

        flexsurvreg(Surv(left, right, type='interval2') ~ gender, data=IR_diabetes, dist="weibull")
        ic_par(Surv(left, right, type = "interval2") ~ gender, data = IR_diabetes, model = "aft", dist = "weibull")

        """
        df = load_diabetes()
        df["gender"] = df["gender"] == "male"
        df["E"] = df["left"] == df["right"]
        df["gender"] = df["gender"].astype(int)

        aft.fit_interval_censoring(df, "left", "right", "E")
        npt.assert_allclose(aft.summary.loc[("lambda_", "gender"), "coef"], 0.04576, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "coef"], np.log(18.31971), rtol=1e-4)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "coef"], np.log(2.82628), rtol=1e-4)

        npt.assert_allclose(aft.log_likelihood_, -2027.196, rtol=1e-3)

        npt.assert_allclose(aft.summary.loc[("lambda_", "gender"), "se(coef)"], 0.02823, rtol=1e-1)

        with pytest.raises(AssertionError):
            npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "se(coef)"], 0.42273, rtol=1e-1)
            npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "se(coef)"], 0.08356, rtol=1e-1)

        aft.fit_interval_censoring(df, "left", "right", "E", ancillary=True)

        npt.assert_allclose(aft.log_likelihood_, -2025.813, rtol=1e-3)

        with pytest.raises(AssertionError):
            npt.assert_allclose(aft.summary.loc[("rho_", "gender"), "coef"], 0.1670, rtol=1e-4)

    def test_interval_censoring_with_ancillary(self, aft):
        df = load_c_botulinum_lag_phase()

        aft.fit_interval_censoring(df, "lower_bound_days", "upper_bound_days", ancillary=df)
        aft.fit_interval_censoring(df, "lower_bound_days", "upper_bound_days", ancillary=True)
        aft.fit_interval_censoring(df, "lower_bound_days", "upper_bound_days", ancillary="pH")

    def test_aft_weibull_with_weights(self, rossi, aft):
        """
        library('flexsurv')
        r = flexsurvreg(Surv(week, arrest) ~ fin + race + wexp + mar + paro + prio, data=df, dist='weibull', weights=age)
        r$coef
        """
        aft.fit(rossi, "week", "arrest", weights_col="age")

        npt.assert_allclose(aft.summary.loc[("lambda_", "fin"), "coef"], 0.3842902, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "race"), "coef"], -0.24538246, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "wexp"), "coef"], 0.31146214, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "mar"), "coef"], 0.47322543, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("lambda_", "paro"), "coef"], -0.02885281, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "prio"), "coef"], -0.06162843, rtol=1e-3)
        npt.assert_allclose(aft.summary.loc[("lambda_", "Intercept"), "coef"], 4.93041526, rtol=1e-2)
        npt.assert_allclose(aft.summary.loc[("rho_", "Intercept"), "coef"], 0.28612353, rtol=1e-4)

    def test_aft_weibull_with_ancillary_model_and_with_weights(self, rossi):
        """
        library('flexsurv')
        r = flexsurvreg(Surv(week, arrest) ~ fin + race + wexp + mar + paro + prio + shape(prio), data=df, dist='weibull', weights=age)
        r$coef
        """
        wf = WeibullAFTFitter(penalizer=0).fit(rossi, "week", "arrest", weights_col="age", ancillary=rossi[["prio"]])

        npt.assert_allclose(wf.summary.loc[("lambda_", "fin"), "coef"], 0.39347, rtol=1e-3)
        npt.assert_allclose(wf.summary.loc[("lambda_", "Intercept"), "coef"], np.log(140.55112), rtol=1e-2)
        npt.assert_allclose(wf.summary.loc[("rho_", "Intercept"), "coef"], np.log(1.25981), rtol=1e-2)
        npt.assert_allclose(wf.summary.loc[("rho_", "prio"), "coef"], 0.01485, rtol=1e-2)

    def test_aft_weibull_can_do_interval_prediction(self, aft):
        # https://github.com/CamDavidsonPilon/lifelines/issues/839
        df = load_diabetes()

        aft = WeibullAFTFitter()
        df["gender"] = df["gender"] == "male"
        df["E"] = df["left"] == df["right"]

        aft.fit_interval_censoring(df, "left", "right", "E")
        aft.predict_survival_function(df)
        aft.print_summary()

        aft = WeibullAFTFitter()
        df = df.drop("E", axis=1)

        aft.fit_interval_censoring(df, "left", "right")
        aft.predict_survival_function(df)
        aft.print_summary()


class TestCoxPHFitter_SemiParametric:
    @pytest.fixture
    def cph(self):
        return SemiParametricPHFitter()

    def test_single_efron_computed_by_hand_examples(self, data_nus, cph):
        X = data_nus[["x"]]
        T = data_nus["t"]
        E = data_nus["E"]
        weights = pd.Series(np.ones_like(T))

        # Want as bools
        E = E.astype(bool)

        # tests from http://courses.nus.edu.sg/course/stacar/internet/st3242/handouts/notes3.pdf
        beta = np.array([0])

        l, u, _ = cph._get_efron_values_single(X, T, E, weights, None, beta)
        l = -l

        assert np.abs(u[0] - -2.51) < 0.05
        assert np.abs(l[0][0] - 77.13) < 0.05
        beta = beta + u / l[0]
        assert np.abs(beta - -0.0326) < 0.05

        l, u, _ = cph._get_efron_values_single(X, T, E, weights, None, beta)
        l = -l

        assert np.abs(l[0][0] - 72.83) < 0.05
        assert np.abs(u[0] - -0.069) < 0.05
        beta = beta + u / l[0]
        assert np.abs(beta - -0.0325) < 0.01

        l, u, _ = cph._get_efron_values_single(X, T, E, weights, None, beta)
        l = -l

        assert np.abs(l[0][0] - 72.70) < 0.01
        assert np.abs(u[0] - -0.000061) < 0.01
        beta = beta + u / l[0]
        assert np.abs(beta - -0.0335) < 0.01

    def test_batch_efron_computed_by_hand_examples(self, data_nus, cph):
        X = data_nus[["x"]]
        T = data_nus["t"]
        E = data_nus["E"]
        weights = pd.Series(np.ones_like(T))

        # Want as bools
        E = E.astype(bool)

        # tests from http://courses.nus.edu.sg/course/stacar/internet/st3242/handouts/notes3.pdf
        beta = np.array([0])

        l, u, _ = cph._get_efron_values_batch(X, T, E, weights, None, beta)
        l = -l

        assert np.abs(u[0] - -2.51) < 0.05
        assert np.abs(l[0][0] - 77.13) < 0.05
        beta = beta + u / l[0]
        assert np.abs(beta - -0.0326) < 0.05

        l, u, _ = cph._get_efron_values_batch(X, T, E, weights, None, beta)
        l = -l

        assert np.abs(l[0][0] - 72.83) < 0.05
        assert np.abs(u[0] - -0.069) < 0.05
        beta = beta + u / l[0]
        assert np.abs(beta - -0.0325) < 0.01

        l, u, _ = cph._get_efron_values_batch(X, T, E, weights, None, beta)
        l = -l

        assert np.abs(l[0][0] - 72.70) < 0.01
        assert np.abs(u[0] - -0.000061) < 0.01
        beta = beta + u / l[0]
        assert np.abs(beta - -0.0335) < 0.01

    def test_efron_newtons_method(self, data_nus, cph):
        cph._batch_mode = False
        newton = cph._newton_raphson_for_efron_model
        X, T, E, W = (data_nus[["x"]], data_nus["t"], data_nus["E"], pd.Series(np.ones_like(data_nus["t"])))
        entries = None

        assert np.abs(newton(X, T, E, W, entries)[0] - -0.0335) < 0.0001


class TestCoxPHFitterPeices:
    @pytest.fixture
    def cph(self):
        return CoxPHFitter(baseline_estimation_method="piecewise", breakpoints=[25])

    def test_baseline_hazard_has_correct_functional_form(self, cph, rossi):
        cph.fit(rossi, "week", "arrest", formula="fin")
        bhz = cph.baseline_hazard_.loc[1, "baseline hazard"]

        npt.assert_allclose(
            bhz,
            np.exp(
                cph.summary.loc[("beta_", "Intercept"), "coef"]
                + cph.summary.loc[("beta_", "fin"), "coef"] * cph._central_values.loc["baseline", "fin"]
            ),
        )

        bhz = cph.baseline_hazard_.loc[rossi["week"].max(), "baseline hazard"]
        npt.assert_allclose(
            bhz,
            np.exp(
                cph.summary.loc[("beta_", "Intercept"), "coef"]
                + cph.summary.loc[("beta_", "fin"), "coef"] * cph._central_values.loc["baseline", "fin"]
            )
            * np.exp(cph.summary.loc[("log_lambda2_", "Intercept"), "coef"]),
        )

    def test_trivial_model_doesnt_fail(self, cph, rossi):
        cph.fit(rossi[["week", "arrest"]], "week", "arrest")
        cph.baseline_hazard_


class TestCoxPHFitter:
    @pytest.fixture
    def cph(self):
        return CoxPHFitter()

    @pytest.fixture
    def cph_spline(self):
        return CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=2)

    @pytest.fixture
    def cph_pieces(self):
        return CoxPHFitter(baseline_estimation_method="piecewise", breakpoints=[25])

    @pytest.mark.xfail
    def test_has_c_index(self, cph_spline, cph_pieces, cph, rossi):
        cph.fit(rossi, "week", "arrest")
        cph_pieces.fit(rossi, "week", "arrest")
        cph_spline.fit(rossi, "week", "arrest")

        assert cph.concordance_index_
        assert cph_pieces.concordance_index_
        assert cph_spline.concordance_index_

    def test_score_function_works_with_formulas(self, rossi):
        cph = CoxPHFitter()
        cph.fit(
            rossi,
            "week",
            "arrest",
            formula=f"bs(age, df=3, lower_bound={rossi.age.min()}, upper_bound={rossi.age.max()}) + race + wexp + mar + paro +prio",
        )
        cph.score(rossi)
        cph.score(rossi, scoring_method="concordance_index")

    def test_fit_kwargs_works_for_semiparametric(self, cph, rossi, capfd):
        cph.fit(rossi, "week", "arrest", fit_options={"step_size": 0.1}, show_progress=True)
        out, err = capfd.readouterr()
        assert "step_size = 0.1000" in out

    def test_fit_kwargs_works_for_spline_model(self, cph_spline, rossi, capfd):
        with pytest.raises(ConvergenceError):
            cph_spline.fit(rossi, "week", "arrest", fit_options={"maxiter": 10}, show_progress=True)

        cph_spline.fit(rossi, "week", "arrest", fit_options={"maxiter": 1000}, show_progress=True)
        assert True

    def test_parametric_models_can_do_interval_censoring(self, cph_spline, cph_pieces):
        df = load_diabetes()
        df["gender"] = df["gender"] == "male"
        df["gender"] = df["gender"].astype(int)
        df["left"] = df["left"]
        df["right"] = df["right"]

        cph_pieces.fit_interval_censoring(df, "left", "right")
        cph_pieces.print_summary()

    def test_parametric_models_can_do_left_censoring(self, cph_spline, cph_pieces):
        df = load_diabetes()
        df = df.drop("left", axis=1)
        df["E"] = 0

        df["gender"] = df["gender"] == "male"
        df["gender"] = df["gender"].astype(int)

        cph_spline = CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=2, penalizer=0.001)
        cph_spline.fit_left_censoring(df, "right", "E", formula="gender + 1")
        cph_spline.print_summary()

        cph_pieces.fit_left_censoring(df, "right", "E")
        cph_spline.print_summary()

    def test_parametric_strata_null_dof(self, cph_spline, cph_pieces, rossi):
        cph_spline.fit(rossi, "week", "arrest", strata="paro", formula="age")
        assert cph_spline._ll_null_dof < cph_spline.params_.shape[0]

        cph_pieces.fit(rossi, "week", "arrest", strata="paro", formula="age")
        assert cph_pieces._ll_null_dof < cph_pieces.params_.shape[0]

    def test_late_entries_where_obs_is_equal_to_entry(self, cph):

        df = load_multicenter_aids_cohort_study()

        df.loc[1, "W"] = df.loc[1, "T"]
        df.loc[1, "D"] = 1

        with pytest.raises(ValueError):
            cph.fit(df, "T", "D", entry_col="W")

        df.loc[1, "T"] = df.loc[1, "T"] + 0.00001
        cph.fit(df, "T", "D", entry_col="W")

    def test_parametric_strata_score(self, cph_spline, cph_pieces, rossi):
        cph_spline.fit(rossi, "week", "arrest", strata="paro", formula="age")
        cph_spline.score(rossi)

        cph_pieces.fit(rossi, "week", "arrest", strata="paro", formula="age")
        cph_pieces.score(rossi)

    def test_formula_can_accept_numpy_functions(self, cph, rossi):
        cph.fit(rossi, "week", "arrest", formula="fin + log10(prio+1) + np.sqrt(age)")
        assert "fin" in cph.summary.index
        assert "log10(prio+1)" in cph.summary.index
        assert "np.sqrt(age)" in cph.summary.index

    @pytest.mark.xfail
    def test_formulas_can_have_np_and_custom_functions(self, rossi, cph):
        def custom_func(x):
            return x + 1

        cph.fit(rossi, "week", "arrest", formula="np.log10(age) + custom_func(age)")
        cph.print_summary()
        assert False

    def test_categorical_variables_are_still_encoded_correctly(self, cph):
        """
        We must drop the intercept in the design matrix, but still have proper dummy encoding
        """

        df = pd.DataFrame({"cat": ["A", "B", "A", "C", "C", "A", "B"], "T": [1.0, 2.0, 3.0, 4, 5, 6, 7]})

        cph.fit(df, "T", formula="C(cat)")
        assert cph.summary.shape[0] == 2

    def test_trival_entry_col(self, rossi):
        cph_without_entry_summary = CoxPHFitter().fit(rossi, "week", "arrest").summary
        cphs_without_entry_summary = (
            CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=2).fit(rossi, "week", "arrest").summary
        )

        rossi["entry"] = 0
        cph_with_entry_summary = CoxPHFitter().fit(rossi, "week", "arrest", entry_col="entry").summary
        cphs_with_entry_summary = (
            CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=2)
            .fit(rossi, "week", "arrest", entry_col="entry")
            .summary
        )

        assert_frame_equal(cph_without_entry_summary, cph_with_entry_summary)
        assert_frame_equal(cphs_without_entry_summary, cphs_with_entry_summary)

    def test_trival_entry_col_with_strata(self, rossi):
        cph_without_entry_summary = CoxPHFitter().fit(rossi, "week", "arrest", strata=["fin"]).summary

        rossi["entry"] = 0
        cph_with_entry_summary = CoxPHFitter().fit(rossi, "week", "arrest", entry_col="entry", strata=["fin"]).summary

        assert_frame_equal(cph_without_entry_summary, cph_with_entry_summary)

    def test_entry_col_against_R(self, cph):
        """
        library(survival)
        df = read.csv("~/code/lifelines/lifelines/datasets/multicenter_aids_cohort.tsv", sep="\t")
        coxph(Surv(W, T, D) ~ AIDSY, data=df)
        """
        df = load_multicenter_aids_cohort_study()
        cph.fit(df, "T", "D", entry_col="W")
        npt.assert_allclose(cph.summary.loc["AIDSY", "coef"], 0.02322, rtol=2)
        npt.assert_allclose(cph.summary.loc["AIDSY", "se(coef)"], 0.24630, rtol=3)
        npt.assert_allclose(cph.log_likelihood_, -95.15478, rtol=2)

    def test_formulas_can_be_used_for_inference(self, rossi, cph, cph_spline, cph_pieces):
        cph.fit(rossi, "week", "arrest", formula="age + race")
        assert cph.summary.index.tolist() == ["age", "race"]

        cph.fit(rossi, "week", "arrest", formula="age * race")
        assert cph.summary.index.tolist() == ["age", "race", "age:race"]

        cph_spline.fit(rossi, "week", "arrest", formula="age + race")
        assert cph_spline.summary.loc["beta_"].index.tolist() == ["Intercept", "age", "race"]

        cph_spline.fit(rossi, "week", "arrest", formula="age * race")
        assert cph_spline.summary.loc["beta_"].index.tolist() == ["Intercept", "age", "race", "age:race"]

        cph_pieces.fit(rossi, "week", "arrest", formula="age + race")
        assert cph_pieces.summary.loc["beta_"].index.tolist() == ["Intercept", "age", "race"]

        cph_pieces.fit(rossi, "week", "arrest", formula="age * race")
        assert cph_pieces.summary.loc["beta_"].index.tolist() == ["Intercept", "age", "race", "age:race"]

    @pytest.mark.parametrize(
        "cph",
        [
            CoxPHFitter(),
            CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=3),
            CoxPHFitter(baseline_estimation_method="piecewise", breakpoints=[25]),
        ],
    )
    def test_formulas_can_be_used_with_prediction(self, rossi, cph):
        cph.fit(rossi, "week", "arrest", formula="age * race")
        cph.predict_survival_function(rossi)

    def test_timeline_argument_can_be_set(self, rossi, cph_spline, cph):
        timeline = np.linspace(0, 100)
        cph.fit(rossi, "week", "arrest", timeline=timeline)

        cph_spline.fit(rossi, "week", "arrest", timeline=timeline)

        npt.assert_allclose(cph_spline.timeline, timeline)
        npt.assert_allclose(cph.timeline, timeline)

        npt.assert_allclose(cph.predict_survival_function(rossi).index.values, timeline)
        npt.assert_allclose(cph_spline.predict_survival_function(rossi).index.values, timeline)

    def test_penalizer_can_be_an_array(self, rossi):

        cph_array = CoxPHFitter(penalizer=0.01 * np.ones(7)).fit(rossi, "week", "arrest")
        cph_float = CoxPHFitter(penalizer=0.01).fit(rossi, "week", "arrest")

        assert_frame_equal(cph_array.summary, cph_float.summary)

    def test_penalizer_can_be_an_array_and_check_it_behaves_as_expected(self, rossi):

        penalty = np.array([0, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01])
        cph_array = CoxPHFitter(penalizer=penalty).fit(rossi, "week", "arrest")
        cph_float = CoxPHFitter(penalizer=0.01).fit(rossi, "week", "arrest")

        assert abs(cph_array.summary.loc["fin", "coef"]) > abs(cph_float.summary.loc["fin", "coef"])

    def test_AIC_partial_(self, cph, rossi):
        cph.fit(rossi, "week", "arrest")
        npt.assert_allclose(cph.AIC_partial_, -2 * cph.log_likelihood_ + 2 * cph.summary.shape[0])

    def test_compute_followup_hazard_ratios(self, cph, cph_spline, rossi):
        cph.fit(rossi, "week", "arrest")
        cph.compute_followup_hazard_ratios(rossi, [15, 25, 35, 45])

        cph_spline.fit(rossi, "week", "arrest")
        cph_spline.compute_followup_hazard_ratios(rossi, [15, 25, 35, 45])

    def test_model_can_accept_null_covariates(self, cph, rossi):
        cph.fit(rossi[["week", "arrest"]], "week", "arrest")
        assert True

    @pytest.mark.parametrize(
        "cph",
        [
            CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=3),
            CoxPHFitter(baseline_estimation_method="piecewise", breakpoints=[25]),
        ],
    )
    def test_parameterized_model_can_handle_specific_outliers(self, cph):
        # https://github.com/CamDavidsonPilon/lifelines/issues/965

        # Generating random correlated data
        mean = [6, 0, 60]
        cov = [[5, 0, 0], [0, 5, 0], [0, 0, 5]]  # diagonal covariance
        days, cov1, cov2 = np.random.multivariate_normal(mean, cov, 5000).T

        # This is probably what causes the issue : some of the data has extreme values
        days[4500:] = np.multiply(days[4500:], 50)

        test_data = pd.DataFrame({"Days": days, "Cov1": cov1, "Cov2": cov2})
        test_data = test_data[test_data["Days"] > 0]

        cph.fit(test_data, duration_col="Days")

        # check survival is always decreasing
        assert np.all(cph.baseline_survival_.diff().dropna() < 0)

    def test_spline_and_breslow_models_offer_very_comparible_baseline_survivals(self, rossi):
        cph_breslow = CoxPHFitter().fit(rossi, "week", "arrest")
        cph_spline = CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=2).fit(rossi, "week", "arrest")

        bh_spline = cph_spline.baseline_survival_at_times()
        bh_breslow = cph_breslow.baseline_survival_

        assert (bh_breslow["baseline survival"] - bh_spline["baseline survival"]).std() < 0.005

    def test_penalty_term_is_used_in_log_likelihood_value(self, rossi):
        assert (
            CoxPHFitter(penalizer=1e-6).fit(rossi, "week", "arrest").log_likelihood_
            < CoxPHFitter(penalizer=1e-8).fit(rossi, "week", "arrest").log_likelihood_
            < CoxPHFitter(penalizer=0).fit(rossi, "week", "arrest").log_likelihood_
        )
        assert (
            CoxPHFitter(penalizer=1e-6, baseline_estimation_method="spline", n_baseline_knots=3)
            .fit(rossi, "week", "arrest")
            .log_likelihood_
            < CoxPHFitter(penalizer=1e-8, baseline_estimation_method="spline", n_baseline_knots=3)
            .fit(rossi, "week", "arrest")
            .log_likelihood_
            < CoxPHFitter(penalizer=0, baseline_estimation_method="spline", n_baseline_knots=3)
            .fit(rossi, "week", "arrest")
            .log_likelihood_
        )

    @pytest.mark.parametrize(
        "cph",
        [
            CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=3),
            CoxPHFitter(baseline_estimation_method="piecewise", breakpoints=[25]),
        ],
    )
    def test_strata_estimation_for_parameterized(self, rossi, cph):
        cph.fit(rossi, "week", "arrest", strata="wexp")

        assert cph.baseline_cumulative_hazard_.shape[1] == 2
        assert cph.baseline_hazard_.shape[1] == 2
        assert cph.baseline_survival_.shape[1] == 2

        cph.fit(rossi, "week", "arrest", strata=["wexp", "paro"])

        assert cph.baseline_cumulative_hazard_.shape[1] == 4
        assert cph.baseline_hazard_.shape[1] == 4
        assert cph.baseline_survival_.shape[1] == 4

    def test_strata_estimation_is_same_if_using_trivial_strata(self, rossi):
        rossi["strata"] = "a"
        trivial_strata_cph = CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=3)
        trivial_strata_cph.fit(rossi, "week", "arrest", strata="strata")

        cph = CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=3)
        cph.fit(rossi.drop("strata", axis=1), "week", "arrest")

        assert_frame_equal(
            cph.summary.loc[[("beta_", "fin"), ("beta_", "mar"), ("beta_", "paro")]],
            trivial_strata_cph.summary.loc[[("beta_", "fin"), ("beta_", "mar"), ("beta_", "paro")]],
            atol=0.05,
        )

        assert_frame_equal(
            cph.summary.loc[[("beta_", "Intercept"), ("phi1_", "Intercept")]].reset_index(drop=True),
            trivial_strata_cph.summary.loc[[("beta_", "Intercept"), ("sa_phi1_", "Intercept")]].reset_index(drop=True),
            atol=0.05,
        )

    @pytest.mark.parametrize(
        "cph",
        [
            CoxPHFitter(baseline_estimation_method="spline", n_baseline_knots=3),
            CoxPHFitter(baseline_estimation_method="piecewise", breakpoints=[25]),
        ],
    )
    def test_baseline_estimation_for_parameteric(self, rossi, cph):
        cph.fit(rossi, "week", "arrest")

        assert isinstance(cph.baseline_survival_, pd.DataFrame)
        assert list(cph.baseline_survival_.columns) == ["baseline survival"]
        assert list(cph.baseline_cumulative_hazard_.columns) == ["baseline cumulative hazard"]

        assert cph.baseline_survival_at_times([1, 2, 3]).shape[0] == 3
        assert cph.baseline_cumulative_hazard_at_times([1, 2, 3]).shape[0] == 3
        assert cph.baseline_hazard_at_times([1, 2, 3]).shape[0] == 3

    def test_conditional_after_in_prediction(self, rossi, cph):
        rossi.loc[rossi["week"] == 1, "week"] = 0
        cph.fit(rossi, "week", "arrest")
        p1 = cph.predict_survival_function(rossi.iloc[0])
        p2 = cph.predict_survival_function(rossi.iloc[0], conditional_after=[8])

        explicit = p1 / p1.loc[8]

        npt.assert_allclose(explicit.loc[8.0, 0], p2.loc[0.0, 0])
        npt.assert_allclose(explicit.loc[10.0, 0], p2.loc[2.0, 0])
        npt.assert_allclose(explicit.loc[12.0, 0], p2.loc[4.0, 0])
        npt.assert_allclose(explicit.loc[20.0, 0], p2.loc[12.0, 0])

    def test_conditional_after_with_custom_times(self, rossi):
        cph_semi = CoxPHFitter(baseline_estimation_method="breslow").fit(rossi, "week", "arrest")
        cph_spline = CoxPHFitter(n_baseline_knots=2, baseline_estimation_method="spline").fit(rossi, "week", "arrest")
        times = np.arange(5)

        # predict single
        result = cph_semi.fit(rossi, "week", "arrest").predict_survival_function(
            rossi.iloc[0], times=times, conditional_after=[10]
        )
        npt.assert_allclose(result.index.values, times)

        result = cph_spline.fit(rossi, "week", "arrest").predict_survival_function(
            rossi.iloc[0], times=times, conditional_after=[10]
        )
        npt.assert_allclose(result.index.values, times)

        # predict multiple
        result = cph_semi.fit(rossi, "week", "arrest").predict_survival_function(
            rossi.iloc[:10], times=times, conditional_after=[10] * 10
        )
        npt.assert_allclose(result.index.values, times)

        result = cph_spline.fit(rossi, "week", "arrest").predict_survival_function(
            rossi.iloc[:10], times=times, conditional_after=[10] * 10
        )
        npt.assert_allclose(result.index.values, times)

    def test_conditional_after_with_strata_in_prediction(self, rossi, cph):
        rossi.loc[rossi["week"] == 1, "week"] = 0
        cph.fit(rossi, "week", "arrest", strata=["fin"])
        p1 = cph.predict_survival_function(rossi.iloc[0])
        p2 = cph.predict_survival_function(rossi.iloc[0], conditional_after=[8])

        explicit = p1 / p1.loc[8]

        npt.assert_allclose(explicit.loc[8.0, 0], p2.loc[0.0, 0])
        npt.assert_allclose(explicit.loc[10.0, 0], p2.loc[2.0, 0])
        npt.assert_allclose(explicit.loc[12.0, 0], p2.loc[4.0, 0])
        npt.assert_allclose(explicit.loc[20.0, 0], p2.loc[12.0, 0])

    def test_conditional_after_with_strata_in_prediction2(self, rossi, cph):

        cph.fit(rossi, duration_col="week", event_col="arrest", strata=["race"])

        censored_subjects = rossi.loc[~rossi["arrest"].astype(bool)]
        censored_subjects_last_obs = censored_subjects["week"]
        pred = cph.predict_survival_function(censored_subjects, conditional_after=censored_subjects_last_obs)

    def test_conditional_after_in_prediction_multiple_subjects(self, rossi, cph):
        rossi.loc[rossi["week"] == 1, "week"] = 0
        cph.fit(rossi, "week", "arrest", strata=["fin"])
        p1 = cph.predict_survival_function(rossi.iloc[[0, 1, 2]])
        p2 = cph.predict_survival_function(rossi.iloc[[0, 1, 2]], conditional_after=[8, 9, 0])

        explicit = p1 / p1.loc[8]

        npt.assert_allclose(explicit.loc[8.0, 0], p2.loc[0.0, 0])
        npt.assert_allclose(explicit.loc[10.0, 0], p2.loc[2.0, 0])
        npt.assert_allclose(explicit.loc[12.0, 0], p2.loc[4.0, 0])
        npt.assert_allclose(explicit.loc[20.0, 0], p2.loc[12.0, 0])

        # no strata
        cph.fit(rossi, "week", "arrest")
        p1 = cph.predict_survival_function(rossi.iloc[[0, 1, 2]])
        p2 = cph.predict_survival_function(rossi.iloc[[0, 1, 2]], conditional_after=[8, 9, 0])

        explicit = p1 / p1.loc[8]

        npt.assert_allclose(explicit.loc[8.0, 0], p2.loc[0.0, 0])
        npt.assert_allclose(explicit.loc[10.0, 0], p2.loc[2.0, 0])
        npt.assert_allclose(explicit.loc[12.0, 0], p2.loc[4.0, 0])
        npt.assert_allclose(explicit.loc[20.0, 0], p2.loc[12.0, 0])

    def test_conditional_after_in_prediction_multiple_subjects_with_custom_times(self, rossi, cph):

        cph.fit(rossi, "week", "arrest")
        p2 = cph.predict_survival_function(rossi.iloc[[0, 1, 2]], conditional_after=[8, 9, 0], times=[10, 20, 30])

        assert p2.index.tolist() == [10.0, 20.0, 30.0]

    def test_that_a_convergence_warning_is_not_thrown_if_using_compute_residuals(self, rossi):
        rossi["c"] = rossi["week"]

        cph = CoxPHFitter(penalizer=1.0)
        cph.fit(rossi, "week", "arrest")

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            cph.compute_residuals(rossi, "martingale")

    def test_that_adding_strata_will_change_c_index(self, cph, rossi):
        """
        df = read.csv('~/code/lifelines/lifelines/datasets/rossi.csv')
        r <- coxph(Surv(week, arrest) ~ fin + age + race + mar + paro + prio + strata(wexp), data=df)
        survConcordance(Surv(week, arrest) ~predict(r) + strata(wexp), df)
        """

        cph.fit(rossi, "week", "arrest")
        c_index_no_strata = cph.concordance_index_

        cph.fit(rossi, "week", "arrest", strata=["wexp"])
        c_index_with_strata = cph.concordance_index_

        assert c_index_with_strata != c_index_no_strata
        npt.assert_allclose(c_index_with_strata, 0.6124492)

    def test_check_assumptions(self, cph, rossi):
        # TODO make this test better
        cph.fit(rossi, "week", "arrest")
        cph.check_assumptions(rossi)

    def test_check_assumptions_for_subset_of_columns(self, cph, rossi):
        cph.fit(rossi, "week", "arrest")
        cph.check_assumptions(rossi, columns=["age"])
        cph.check_assumptions(rossi, columns=[])
        cph.check_assumptions(rossi, columns=["age", "fin"])

    def test_check_assumptions_with_formulas(self, cph, rossi):
        cph.fit(rossi, "week", "arrest", formula="bs(age, df=3) + fin * wexp")
        cph.check_assumptions(rossi)

    def test_cph_doesnt_modify_original_dataframe(self, cph):
        df = pd.DataFrame(
            {
                "var1": [-0.71163379, -0.87481227, 0.99557251, -0.83649751, 1.42737105],
                "T": [5, 6, 7, 8, 9],
                "E": [1, 1, 1, 1, 1],
                "W": [1, 1, 1, 1, 1],
            }
        )

        cph.fit(df, "T", "E", weights_col="W")
        assert df.dtypes["E"] in (int, np.dtype("int64"))
        assert df.dtypes["W"] in (int, np.dtype("int64"))
        assert df.dtypes["T"] in (int, np.dtype("int64"))

    def test_cph_will_handle_times_with_only_censored_individuals(self, rossi):
        rossi_29 = rossi.iloc[0:10].copy()
        rossi_29["week"] = 29
        rossi_29["arrest"] = False

        cph1_summary = CoxPHFitter().fit(rossi.append(rossi_29), "week", "arrest").summary

        cph2_summary = CoxPHFitter().fit(rossi, "week", "arrest").summary

        assert cph2_summary["coef"].iloc[0] != cph1_summary["coef"].iloc[0]

    def test_schoenfeld_residuals_no_strata_but_with_censorship(self, cph):
        """
        library(survival)
        df <- data.frame(
          "var" = c(-0.71163379, -0.87481227,  0.99557251, -0.83649751,  1.42737105),
          "T" = c(5, 6, 7, 8, 9),
          "E" = c(1, 1, 1, 1, 1),
        )

        c = coxph(formula=Surv(T, E) ~ var , data=df)
        residuals(c, "schoen")
        """
        df = pd.DataFrame(
            {"var1": [-0.71163379, -0.87481227, 0.99557251, -0.83649751, 1.42737105], "T": [5, 6, 7, 8, 9], "E": [1, 1, 1, 1, 1]}
        )

        cph.fit(df, "T", "E")

        results = cph.compute_residuals(df, "schoenfeld")
        expected = pd.DataFrame(
            [-0.2165282492, -0.4573005808, 1.1117589644, -0.4379301344, 0.0], columns=pd.Index(["var1"], name="covariate")
        )
        assert_frame_equal(results, expected, atol=0.001)

    def test_schoenfeld_residuals_with_censorship_and_ties(self, cph):
        """
        library(survival)
        df <- data.frame(
          "var" = c(-0.71163379, -0.87481227,  0.99557251, -0.83649751,  1.42737105),
          "T" = c(6, 6, 7, 8, 9),
          "E" = c(1, 1, 1, 0, 1),
        )

        c = coxph(formula=Surv(T, E) ~ var , data=df)
        residuals(c, "schoen")
        """
        df = pd.DataFrame(
            {"var1": [-0.71163379, -0.87481227, 0.99557251, -0.83649751, 1.42737105], "T": [6, 6, 7, 8, 9], "E": [1, 1, 1, 0, 1]}
        )

        cph.fit(df, "T", "E")
        cph.print_summary()
        results = cph.compute_residuals(df, "schoenfeld")
        expected = pd.DataFrame(
            [-0.3903793341, -0.5535578141, 0.9439371482, 0.0], columns=pd.Index(["var1"], name="covariate"), index=[0, 1, 2, 4]
        )
        assert_frame_equal(results, expected, atol=0.001)

    def test_schoenfeld_residuals_with_weights(self, cph):
        """
        library(survival)
        df <- data.frame(
          "var" = c(-0.71163379, -0.87481227,  0.99557251, -0.83649751,  1.42737105),
          "T" = c(6, 6, 7, 8, 9),
          "E" = c(1, 1, 1, 0, 1),
        )

        c = coxph(formula=Surv(T, E) ~ var , data=df)
        residuals(c, "schoen")
        """
        df = pd.DataFrame(
            {
                "var1": [-0.71163379, -0.87481227, 0.99557251, -0.83649751, 1.42737105],
                "T": [5, 6, 7, 8, 9],
                "E": [1, 1, 1, 1, 1],
                "w": [0.5, 1.0, 3.0, 1.0, 1.0],
            }
        )

        cph.fit(df, "T", "E", weights_col="w", robust=True)

        results = cph.compute_residuals(df, "schoenfeld")
        expected = pd.DataFrame(
            [-0.6633324862, -0.9107785234, 0.6176009038, -0.6103579448, 0.0], columns=pd.Index(["var1"], name="covariate")
        )
        assert_frame_equal(results, expected, atol=0.001)

    def test_schoenfeld_residuals_with_strata(self, cph):
        """
        library(survival)
        df <- data.frame(
          "var" = c(-0.71163379, -0.87481227,  0.99557251, -0.83649751,  1.42737105),
          "T" = c( 6, 6, 7, 8, 9),
          "E" = c(1, 1, 1, 1, 1),
          "s" = c(1, 2, 2, 1, 1)
        )

        c = coxph(formula=Surv(T, E) ~ var + stata(s), data=df)
        residuals(c, "schoen")
        """

        df = pd.DataFrame(
            {
                "var1": [-0.71163379, -0.87481227, 0.99557251, -0.83649751, 1.42737105],
                "T": [6, 6, 7, 8, 9],
                "E": [1, 1, 1, 1, 1],
                "s": [1, 2, 2, 1, 1],
            }
        )

        cph.fit(df, "T", "E", strata=["s"])

        results = cph.compute_residuals(df, "schoenfeld")
        expected = pd.DataFrame(
            [5.898252711e-02, -2.074325854e-02, 0.0, -3.823926885e-02, 0.0],
            columns=pd.Index(["var1"], name="covariate"),
            index=[0, 3, 4, 1, 2],
        )
        assert_frame_equal(results, expected, atol=0.001)

    def test_schoenfeld_residuals_with_first_subjects_censored(self, rossi, cph):
        rossi.loc[rossi["week"] == 1, "arrest"] = 0

        cph.fit(rossi, "week", "arrest")
        cph.compute_residuals(rossi, "schoenfeld")

    def test_scaled_schoenfeld_residuals_against_R(self, regression_dataset, cph):
        """
        NOTE: lifelines does not add the coefficients to the final results, but R does when you call residuals(c, "scaledsch")
        """

        cph.fit(regression_dataset, "T", "E")

        results = cph.compute_residuals(regression_dataset, "scaled_schoenfeld") + cph.params_.values
        npt.assert_allclose(results.iloc[0].values, [0.785518935413, 0.862926592959, 2.479586809860], rtol=1e-03)
        npt.assert_allclose(results.iloc[1].values, [-0.888580165064, -1.037904485796, -0.915334612372], rtol=1e-03)
        npt.assert_allclose(
            results.iloc[results.shape[0] - 1].values, [0.222207366875, 0.050957334886, 0.218314242931], rtol=1e-03
        )

    def test_scaled_schoenfeld_residuals_against_R_with_weights(self, regression_dataset, cph):
        """

        library(survival)
        df <- data.frame(
          "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
          "T" = c(5.269797, 6.601666, 7.335846, 11.684092, 12.678458),
          "E" = c(1, 1, 1, 1, 1),
          "w" = c(1, 1, 2, 1, 1)
        )

        c = coxph(formula=Surv(T, E) ~ var1 , data=df, weights=w, ties="efron")
        residuals(c, "scaledsch", weighted=TRUE)
        """
        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "T": [5.269797, 6.601666, 7.335846, 11.684092, 12.678458],
                "w": [1, 1, 2, 1, 1],
            }
        )
        df["E"] = True

        cph.fit(df, "T", "E", weights_col="w")
        cph.print_summary()
        # NOTE: lifelines does not add the coefficients to the final results, but R does when you call residuals(c, "scaledsch")
        results = cph.compute_residuals(df, "scaled_schoenfeld") + cph.params_.values
        npt.assert_allclose(results.iloc[0].values[0], -6.467889, rtol=1e-03)
        npt.assert_allclose(results.iloc[1].values, [9.943385], rtol=1e-03)
        npt.assert_allclose(results.iloc[results.shape[0] - 1].values, [1.229892], rtol=1e-03)

    def test_original_index_is_respected_in_all_residual_tests(self, cph):

        df = pd.DataFrame(
            {"var1": [-0.71163379, -0.87481227, 0.99557251, -0.83649751, 1.42737105], "T": [6, 6, 7, 8, 9], "s": [1, 2, 2, 1, 1]}
        )
        df.index = ["A", "B", "C", "D", "E"]

        cph.fit(df, "T")

        for kind in {"martingale", "schoenfeld", "score", "delta_beta", "deviance"}:
            resids = cph.compute_residuals(df, kind)
            assert resids.sort_index().index.tolist() == ["A", "B", "C", "D", "E"]

    def test_original_index_is_respected_in_all_residual_tests_with_strata(self, cph):

        df = pd.DataFrame(
            {"var1": [-0.71163379, -0.87481227, 0.99557251, -0.83649751, 1.42737105], "T": [6, 6, 7, 8, 9], "s": [1, 2, 2, 1, 1]}
        )
        df.index = ["A", "B", "C", "D", "E"]

        cph.fit(df, "T", strata=["s"])

        for kind in {"martingale", "schoenfeld", "score", "delta_beta", "deviance", "scaled_schoenfeld"}:
            resids = cph.compute_residuals(df, kind)
            assert resids.sort_index().index.tolist() == ["A", "B", "C", "D", "E"]

    def test_martingale_residuals(self, regression_dataset, cph):

        cph.fit(regression_dataset, "T", "E")

        results = cph.compute_residuals(regression_dataset, "martingale")
        npt.assert_allclose(results.loc[0, "martingale"], -2.315035744901, rtol=1e-05)
        npt.assert_allclose(results.loc[1, "martingale"], 0.774216356429, rtol=1e-05)
        npt.assert_allclose(results.loc[199, "martingale"], 0.868510420157, rtol=1e-05)

    def test_strata_will_work_with_matched_pairs(self, rossi, cph):
        rossi["matched_pairs"] = np.floor(rossi.index / 2.0).astype(int)
        cph.fit(rossi, duration_col="week", event_col="arrest", strata=["matched_pairs"], show_progress=True)
        assert cph.baseline_cumulative_hazard_.shape[1] == 216

    def test_print_summary_with_decimals(self, rossi, cph):
        import sys

        saved_stdout = sys.stdout
        try:

            out = StringIO()
            sys.stdout = out

            cph = CoxPHFitter()
            cph.fit(rossi, duration_col="week", event_col="arrest", batch_mode=True)
            cph._time_fit_was_called = "2018-10-23 02:40:45 UTC"
            cph.print_summary(decimals=1)
            output_dec_1 = out.getvalue().strip().split()

            cph.print_summary(decimals=3)
            output_dec_3 = out.getvalue().strip().split()

            assert output_dec_1 != output_dec_3
        finally:
            sys.stdout = saved_stdout
            cph.fit(rossi, duration_col="week", event_col="arrest", batch_mode=False)

    def test_print_summary(self, rossi, cph):

        import sys

        saved_stdout = sys.stdout
        try:
            out = StringIO()
            sys.stdout = out

            cph.fit(rossi, duration_col="week", event_col="arrest")
            cph._time_fit_was_called = "2018-10-23 02:40:45 UTC"
            cph.print_summary()
            output = out.getvalue().strip().split()
            expected = (
                (
                    repr(cph)
                    + "\n"
                    + """
<lifelines.CoxPHFitter: fitted with 432 total observations, 318 right-censored observations>
             duration col = 'week'
                event col = 'arrest'
      baseline estimation = breslow
   number of observations = 432
number of events observed = 114
   partial log-likelihood = -658.75
         time fit was run = 2018-10-23 02:40:45 UTC

---
       coef  exp(coef)   se(coef)   coef lower 95%   coef upper 95%  exp(coef) lower 95%  exp(coef) upper 95%
fin   -0.38       0.68       0.19            -0.75            -0.00                 0.47                 1.00
age   -0.06       0.94       0.02            -0.10            -0.01                 0.90                 0.99
race   0.31       1.37       0.31            -0.29             0.92                 0.75                 2.50
wexp  -0.15       0.86       0.21            -0.57             0.27                 0.57                 1.30
mar   -0.43       0.65       0.38            -1.18             0.31                 0.31                 1.37
paro  -0.08       0.92       0.20            -0.47             0.30                 0.63                 1.35
prio   0.09       1.10       0.03             0.04             0.15                 1.04                 1.16

         z      p   -log2(p)
fin  -1.98   0.05       4.40
age  -2.61   0.01       6.79
race  1.02   0.31       1.70
wexp -0.71   0.48       1.06
mar  -1.14   0.26       1.97
paro -0.43   0.66       0.59
prio  3.19 <0.005       9.48
---
Concordance = 0.64
Partial AIC = 1331.50
log-likelihood ratio test = 33.27 on 7 df
-log2(p) of ll-ratio test = 15.37
"""
                )
                .strip()
                .split()
            )
            for i in [0, 1, 2, 3, -2, -1, -3, -4, -5]:
                assert output[i] == expected[i]
        finally:
            sys.stdout = saved_stdout

    def test_print_summary_with_styles(self, rossi, cph):
        cph.fit(rossi, duration_col="week", event_col="arrest")
        cph.print_summary(style="html")
        cph.print_summary(style="latex")
        cph.print_summary(style="ascii")

    def test_log_likelihood(self, data_nus, cph):
        cph.fit(data_nus, duration_col="t", event_col="E")
        assert abs(cph.log_likelihood_ - -12.7601409152) < 0.001

    def test_fit_method(self, data_nus, cph):
        cph.fit(data_nus, duration_col="t", event_col="E")
        assert np.abs(cph.params_.iloc[0] - -0.0335) < 0.0001

    def test_using_dataframes_vs_numpy_arrays(self, data_pred2, cph):
        cph.fit(data_pred2, "t", "E")

        X = data_pred2[data_pred2.columns.difference(["t", "E"])]
        assert_series_equal(cph.predict_partial_hazard(np.array(X)), cph.predict_partial_hazard(X))

    def test_prediction_methods_will_accept_a_times_arg_to_reindex_the_predictions(self, data_pred2, cph):
        cph.fit(data_pred2, duration_col="t", event_col="E")
        times_of_interest = np.arange(0, 10, 0.5)

        actual_index = cph.predict_survival_function(data_pred2.drop(["t", "E"], axis=1), times=times_of_interest).index
        np.testing.assert_allclose(actual_index.values, times_of_interest)

        actual_index = cph.predict_cumulative_hazard(data_pred2.drop(["t", "E"], axis=1), times=times_of_interest).index
        np.testing.assert_allclose(actual_index.values, times_of_interest)

    def test_data_normalization(self, data_pred2, cph):
        # During fit, CoxPH copies the training data and normalizes it.
        # Future calls should be normalized in the same way and

        cph.fit(data_pred2, duration_col="t", event_col="E")

        # Internal training set
        ci_trn = cph.concordance_index_
        # New data should normalize in the exact same way
        ci_org = concordance_index(data_pred2["t"], -cph.predict_partial_hazard(data_pred2[["x1", "x2"]]).values, data_pred2["E"])

        assert ci_org == ci_trn

    def test_cox_ph_prediction_with_series(self, rossi, cph):
        cph.fit(rossi, duration_col="week", event_col="arrest")
        rossi_mean = rossi.mean()
        result = cph.predict_survival_function(rossi_mean)
        assert_series_equal(cph.baseline_survival_["baseline survival"], result[0], check_names=False)

    @pytest.mark.xfail
    def test_cox_ph_prediction_with_series_of_longer_length(self, rossi, cph):
        rossi = rossi[["week", "arrest", "age"]]
        cph.fit(rossi, duration_col="week", event_col="arrest")

        X = pd.Series([1, 2, 3, 4, 5])
        result = cph.predict_survival_function(X)

    def test_cox_ph_prediction_monotonicity(self, data_pred2):
        # Concordance wise, all prediction methods should be monotonic versions
        # of one-another, unless numerical factors screw it up.
        t = data_pred2["t"]
        e = data_pred2["E"]
        X = data_pred2[["x1", "x2"]]

        cf = CoxPHFitter()
        cf.fit(data_pred2, duration_col="t", event_col="E")

        # Base comparison is partial_hazards
        ci_ph = concordance_index(t, -cf.predict_partial_hazard(X).values, e)

        ci_med = concordance_index(t, cf.predict_median(X).squeeze(), e)
        # pretty close.
        assert abs(ci_ph - ci_med) < 0.001

        ci_exp = concordance_index(t, cf.predict_expectation(X).squeeze(), e)
        assert ci_ph == ci_exp

    def test_crossval_for_cox_ph_with_normalizing_times(self, data_pred2, data_pred1):
        cf = CoxPHFitter()

        for data_pred in [data_pred1, data_pred2]:

            # why does this
            data_norm = data_pred.copy()
            times = data_norm["t"]
            # Normalize to mean = 0 and standard deviation = 1
            times -= np.mean(times)
            times /= np.std(times)
            data_norm["t"] = times

            scores = k_fold_cross_validation(
                cf, data_norm, duration_col="t", event_col="E", k=3, scoring_method="concordance_index"
            )

            mean_score = np.mean(scores)

            expected = 0.9
            msg = "Expected min-mean c-index {:.2f} < {:.2f}"
            assert mean_score > expected, msg.format(expected, mean_score)

    def test_crossval_for_cox_ph(self, data_pred2, data_pred1):
        cf = CoxPHFitter()

        for data_pred in [data_pred1, data_pred2]:
            scores = k_fold_cross_validation(
                cf, data_pred, duration_col="t", event_col="E", k=3, scoring_method="concordance_index"
            )

            mean_score = np.mean(scores)

            expected = 0.9
            msg = "Expected min-mean c-index {:.2f} < {:.2f}"
            assert mean_score > expected, msg.format(expected, mean_score)

    def test_crossval_for_cox_ph_normalized(self, data_pred2, data_pred1):
        cf = CoxPHFitter()
        for data_pred in [data_pred1, data_pred2]:
            data_norm = data_pred.copy()

            times = data_norm["t"]
            # Normalize to mean = 0 and standard deviation = 1
            times -= np.mean(times)
            times /= np.std(times)
            data_norm["t"] = times

            x1 = data_norm["x1"]
            x1 -= np.mean(x1)
            x1 /= np.std(x1)
            data_norm["x1"] = x1

            if "x2" in data_norm.columns:
                x2 = data_norm["x2"]
                x2 -= np.mean(x2)
                x2 /= np.std(x2)
                data_norm["x2"] = x2

            scores = k_fold_cross_validation(
                cf, data_norm, duration_col="t", event_col="E", k=3, scoring_method="concordance_index"
            )

            mean_score = np.mean(scores)
            expected = 0.9
            msg = "Expected min-mean c-index {:.2f} < {:.2f}"
            assert mean_score > expected, msg.format(expected, mean_score)

    def test_coef_output_against_R_super_accurate(self, rossi):
        """
        from http://cran.r-project.org/doc/contrib/Fox-Companion/appendix-cox-regression.pdf
        Link is now broken, but this is the code:

        library(survival)
        rossi <- read.csv('.../lifelines/datasets/rossi.csv')
        r <- coxph(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio,
            data=rossi)
        cat(round(r$coefficients, 8), sep=", ")
        """
        expected = np.array([-0.3794222, -0.0574377, 0.3138998, -0.1497957, -0.4337039, -0.0848711, 0.0914971])
        cf = CoxPHFitter()
        cf.fit(rossi, duration_col="week", event_col="arrest", show_progress=True, batch_mode=True)
        npt.assert_array_almost_equal(cf.params_.values, expected, decimal=6)

        cf.fit(rossi, duration_col="week", event_col="arrest", show_progress=True, batch_mode=False)
        npt.assert_array_almost_equal(cf.params_.values, expected, decimal=6)

    def test_coef_output_against_R_with_strata_super_accurate(self, rossi):
        """
        from http://cran.r-project.org/doc/contrib/Fox-Companion/appendix-cox-regression.pdf
        Link is now broken, but this is the code:

        library(survival)
        rossi <- read.csv('.../lifelines/datasets/rossi.csv')
        r <- coxph(Surv(week, arrest) ~ fin + age + strata(race) + wexp + mar + paro + prio,
            data=rossi)
        cat(round(r$coefficients, 4), sep=", ")
        """
        expected = np.array([-0.3788, -0.0576, -0.1427, -0.4388, -0.0858, 0.0922])
        cf = CoxPHFitter()
        cf.fit(rossi, duration_col="week", event_col="arrest", strata=["race"], show_progress=True, batch_mode=True)
        npt.assert_array_almost_equal(cf.params_.values, expected, decimal=4)

    def test_coef_output_against_R_using_non_trivial_but_integer_weights(self, rossi):
        rossi_ = rossi.copy()
        rossi_["weights"] = 1.0
        rossi_ = rossi_.groupby(rossi.columns.tolist())["weights"].sum().reset_index()

        expected = np.array([-0.3794, -0.0574, 0.3139, -0.1498, -0.4337, -0.0849, 0.0915])
        cf = CoxPHFitter()
        cf.fit(rossi_, duration_col="week", event_col="arrest", weights_col="weights")
        npt.assert_array_almost_equal(cf.params_.values, expected, decimal=4)

    def test_robust_errors_with_trivial_weights_is_the_same_than_R(self):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "T" = c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666)
        )
        df['E'] = 1
        df['var3'] = 0.75
        r = coxph(formula=Surv(T, E) ~ var1 + var2, data=df, weights=var3, robust=TRUE)
        r$var
        r$naive.var
        """

        w = 0.75
        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "var2": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "T": [7.335846, 5.269797, 11.684092, 12.678458, 6.601666],
            }
        )
        df["E"] = 1
        df["var3"] = w

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", robust=True, weights_col="var3", show_progress=True)
        expected = pd.Series({"var1": 7.680, "var2": -0.915})
        assert_series_equal(cph.params_, expected, atol=0.01, check_names=False)

        expected_cov = np.array([[33.079106, -5.964652], [-5.964652, 2.040642]])
        npt.assert_array_almost_equal(w * cph.variance_matrix_, expected_cov, decimal=1)

        expected = pd.Series({"var1": 2.097, "var2": 0.827})
        assert_series_equal(cph.summary["se(coef)"], expected, atol=0.01, check_names=False)

    def test_delta_betas_are_the_same_as_in_R(self):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "T" = c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666)
        )
        df['E'] = 1
        r = coxph(formula=Surv(T, E) ~ var1, data=df, robust=TRUE)
        residuals(r, 'dfbeta')
        """

        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "T": [5.269797, 6.601666, 7.335846, 11.684092, 12.678458],
            }
        )
        df["E"] = True
        df["weights"] = 1
        df = df.sort_values(by="T")

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", show_progress=True, weights_col="weights")

        X = normalize(df.drop(["T", "E", "weights"], axis=1), cph._norm_mean, cph._norm_std)

        expected = np.array([[-1.1099688, 0.6620063, 0.4630473, 0.5807250, -0.5958099]]).T
        actual = cph._compute_delta_beta(X, df["T"], df["E"], df["weights"])
        npt.assert_allclose(expected, actual, rtol=0.001)

    def test_delta_betas_with_strata_are_the_same_as_in_R(self):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "T" = c(5.269797, 6.601666, 7.335846, 11.684092, 12.678458),
            "strata" = c(1, 1, 1, 2, 2),
        )
        df['E'] = 1
        r = coxph(formula=Surv(T, E) ~ var1 + strata(strata), data=df, robust=TRUE)
        residuals(r, 'dfbeta')
        """

        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "T": [5.269797, 6.601666, 7.335846, 11.684092, 12.678458],
                "strata": [1, 1, 1, 2, 2],
            }
        )
        df["E"] = True
        df["weights"] = 1
        df = df.sort_values(by="T")

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", show_progress=True, weights_col="weights", strata=["strata"])

        df = df.set_index("strata")
        X = normalize(df.drop(["T", "E", "weights"], axis=1), 0, cph._norm_std)

        expected = np.array([[-0.6960789, 1.6729761, 0.3094744, -0.2895864, -0.9967852]]).T
        actual = cph._compute_delta_beta(X, df["T"], df["E"], df["weights"])
        npt.assert_allclose(expected, actual, rtol=0.001)

    def test_delta_betas_with_weights_are_the_same_as_in_R(self):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "T" = c(5.269797, 6.601666, 7.335846, 11.684092, 12.678458),
            "w" = c(1, 0.5, 2, 1, 1)
        )
        df['E'] = 1
        r = coxph(formula=Surv(T, E) ~ var1 + strata(strata), data=df, weights=w)
        residuals(r, 'dfbeta')
        """

        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "T": [5.269797, 6.601666, 7.335846, 11.684092, 12.678458],
                "weights": [1, 0.5, 2, 1, 1],
            }
        )
        df["E"] = True
        df = df.sort_values(by="T")

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", show_progress=True, weights_col="weights", robust=True)

        X = normalize(df.drop(["T", "E", "weights"], axis=1), 0, cph._norm_std)

        expected = np.array([[-1.1156470, 0.7698781, 0.3923246, 0.8040079, -0.8505637]]).T
        actual = cph._compute_delta_beta(X, df["T"], df["E"], df["weights"])
        npt.assert_allclose(expected, actual, rtol=0.001)

    def test_cluster_option(self):
        """
        library(survival)
        df <- data.frame(
          "var1" = c(1, 1, 2, 2, 2),
          "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
          "id" = c(1, 1, 2, 3, 4),
          "T" = c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666)
        )
        df['E'] = 1

        c = coxph(formula=Surv(T, E) ~ var1 + var2 + cluster(id), data=df)
        """

        df = pd.DataFrame(
            {
                "var1": [1, 1, 2, 2, 2],
                "var2": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "T": [7.335846, 5.269797, 11.684092, 12.678458, 6.601666],
                "id": [1, 1, 2, 3, 4],
            }
        )
        df["E"] = 1

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", cluster_col="id", show_progress=True)
        expected = pd.Series({"var1": 5.9752, "var2": 4.0683})
        assert_series_equal(cph.summary["se(coef)"], expected, atol=0.01, check_names=False)

    def test_cluster_option_with_strata(self, regression_dataset):
        """
        library(survival)
        df <- data.frame(
          "var" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
          "id" =     c(1, 1, 2, 3, 4),
          "strata" = c(1, 1, 2, 2, 2),
          "T" = c( 5.269797, 6.601666, 7.335846, 11.684092, 12.678458)
        )
        df['E'] = 1

        c = coxph(formula=Surv(T, E) ~ strata(strata) + var + cluster(id), data=df)
        """

        df = pd.DataFrame(
            {
                "var": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "id": [1, 1, 2, 3, 4],
                "strata": [1, 1, 2, 2, 2],
                "T": [5.269797, 6.601666, 7.335846, 11.684092, 12.678458],
            }
        )
        df["E"] = 1

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", cluster_col="id", strata=["strata"], show_progress=True)
        expected = pd.Series({"var": 0.643})
        assert_series_equal(cph.summary["se(coef)"], expected, atol=0.01, check_names=False)

    def test_robust_errors_with_less_trival_weights_is_the_same_as_R(self, regression_dataset):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "T" = c(1, 2, 3, 4, 5)
        )
        df['E'] = 1
        df['var3'] = 2
        df[4, 'var3'] = 1
        r = coxph(formula=Surv(T, E) ~ var1 + var2, data=df, weights=var3, robust=TRUE)
        r$var
        r$naive.var
        residuals(r, type='dfbeta')
        """

        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "var2": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "T": [1, 2, 3, 4, 5],
                "var3": [2, 2, 2, 1, 2],
            }
        )
        df["E"] = 1

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", robust=True, weights_col="var3", show_progress=True)
        expected = pd.Series({"var1": 1.431, "var2": -1.277})
        assert_series_equal(cph.params_, expected, atol=0.01, check_names=False)

        expected_cov = np.array([[3.5439245, -0.3549099], [-0.3549099, 0.4499553]])
        npt.assert_array_almost_equal(
            cph.variance_matrix_, expected_cov, decimal=1
        )  # not as precise because matrix inversion will accumulate estimation errors.

        expected = pd.Series({"var1": 2.094, "var2": 0.452})
        assert_series_equal(cph.summary["se(coef)"], expected, atol=0.01, check_names=False)

    def test_robust_errors_with_non_trivial_weights_is_the_same_as_R(self, regression_dataset):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "var3" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "T" =    c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666)
        )
        df['E'] = 1
        r = coxph(formula=Surv(T, E) ~ var1 + var2, data=df, weights=var3, robust=TRUE)
        r$var
        r$naive.var
        """

        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "var2": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "var3": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "T": [7.335846, 5.269797, 11.684092, 12.678458, 6.601666],
            }
        )
        df["E"] = 1

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", robust=True, weights_col="var3", show_progress=True)
        expected = pd.Series({"var1": -5.16231, "var2": 1.71924})
        assert_series_equal(cph.params_, expected, atol=0.1, check_names=False)

        expected = pd.Series({"var1": 9.97730, "var2": 2.45648})
        assert_series_equal(cph.summary["se(coef)"], expected, atol=0.01, check_names=False)

    def test_robust_errors_with_non_trivial_weights_with_censorship_is_the_same_as_R(self, regression_dataset):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "var3" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "T" =    c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666),
            "E" =    c(1, 1, 0, 1, 1)
        )
        r = coxph(formula=Surv(T, E) ~ var1 + var2, data=df, weights=var3, robust=TRUE)
        r$var
        r$naive.var
        """

        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "var2": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "var3": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "T": [7.335846, 5.269797, 11.684092, 12.678458, 6.601666],
                "E": [1, 1, 0, 1, 1],
            }
        )

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", robust=True, weights_col="var3", show_progress=True)
        expected = pd.Series({"var1": -8.360533, "var2": 1.781126})
        assert_series_equal(cph.params_, expected, atol=0.001, check_names=False)

        expected = pd.Series({"var1": 12.303338, "var2": 2.395670})
        assert_series_equal(cph.summary["se(coef)"], expected, atol=0.001, check_names=False)

    def test_robust_errors_is_the_same_as_R(self, regression_dataset):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "T" = c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666)
        )
        df['E'] = 1

        coxph(formula=Surv(T, E) ~ var1 + var2, data=df, robust=TRUE)
        """

        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "var2": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "T": [7.335846, 5.269797, 11.684092, 12.678458, 6.601666],
            }
        )
        df["E"] = 1

        cph = CoxPHFitter()
        cph.fit(df, "T", "E", robust=True, show_progress=True)
        expected = pd.Series({"var1": 7.680, "var2": -0.915})
        assert_series_equal(cph.params_, expected, atol=0.01, check_names=False)

        expected = pd.Series({"var1": 2.097, "var2": 0.827})
        assert_series_equal(cph.summary["se(coef)"], expected, atol=0.01, check_names=False)

    def test_compute_likelihood_ratio_test_is_different_if_weights_are_provided(self, regression_dataset):
        cph = CoxPHFitter()
        cph.fit(regression_dataset, "T", "E")

        without_weights = cph.log_likelihood_ratio_test()

        regression_dataset["weights"] = 0.5
        cph = CoxPHFitter()

        with pytest.warns(StatisticalWarning, match="weights are not integers"):

            cph.fit(regression_dataset, "T", "E", weights_col="weights")

            with_weights = cph.log_likelihood_ratio_test()
            assert with_weights.test_statistic != without_weights.test_statistic

    def test_log_likelihood_test_against_R_with_weights(self, rossi):
        """
        df <- data.frame(
          "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
          "T" = c(5.269797, 6.601666, 7.335846, 11.684092, 12.678458),
          "w" = c(1, 0.5, 2, 1, 1)
        )
        df['E'] = 1
        r = coxph(formula=Surv(T, E) ~ var1, data=df, weights=w)
        summary(r)
        """
        df = pd.DataFrame(
            {
                "var1": [0.209325, 0.693919, 0.443804, 0.065636, 0.386294],
                "T": [5.269797, 6.601666, 7.335846, 11.684092, 12.678458],
                "w": [1, 0.5, 2, 1, 1],
            }
        )
        df["E"] = True

        cph = CoxPHFitter()
        with pytest.warns(StatisticalWarning, match="weights are not integers"):
            cph.fit(df, "T", "E", show_progress=True, weights_col="w")
            expected = 0.05
            assert abs(cph.log_likelihood_ratio_test().test_statistic - expected) < 0.01

    def test_trival_float_weights_with_no_ties_is_the_same_as_R(self, regression_dataset):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "T" = c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666)
        )
        df['E'] = 1
        df['var3'] = 0.75

        coxph(formula=Surv(T, E) ~ var1 + var2, data=df, weights=var3)
        """
        df = regression_dataset
        ix = df["var3"] < 1.0
        df = df.loc[ix].head()
        df["var3"] = [0.75] * 5

        cph = CoxPHFitter()
        with pytest.warns(StatisticalWarning, match="weights are not integers"):

            cph.fit(df, "T", "E", weights_col="var3", show_progress=True)

            expected_coef = pd.Series({"var1": 7.680, "var2": -0.915})
            assert_series_equal(cph.params_, expected_coef, atol=0.01, check_names=False)

            expected_std = pd.Series({"var1": 6.641, "var2": 1.650})
            assert_series_equal(cph.summary["se(coef)"], expected_std, atol=0.01, check_names=False)

            expected_ll = -1.142397
            assert abs(cph.log_likelihood_ - expected_ll) < 0.001

    def test_less_trival_float_weights_with_no_ties_is_the_same_as_R(self, regression_dataset):
        """
        df <- data.frame(
            "var1" = c(0.209325, 0.693919, 0.443804, 0.065636, 0.386294),
            "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "T" = c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666)
        )
        df['E'] = 1
        df['var3'] = 0.75
        df[1, 'var3'] = 1.75

        coxph(formula=Surv(T, E) ~ var1 + var2, data=df, weights=var3)
        """
        df = regression_dataset
        ix = df["var3"] < 1.0
        df = df.loc[ix].head()
        df["var3"] = [1.75] + [0.75] * 4

        cph = CoxPHFitter()
        with pytest.warns(StatisticalWarning, match="weights are not integers"):

            cph.fit(df, "T", "E", weights_col="var3", show_progress=True)
            expected = pd.Series({"var1": 7.995, "var2": -1.154})
            assert_series_equal(cph.params_, expected, atol=0.01, check_names=False)

            expected = pd.Series({"var1": 6.690, "var2": 1.614})
            assert_series_equal(cph.summary["se(coef)"], expected, atol=0.01, check_names=False)

    def test_non_trival_float_weights_with_no_ties_is_the_same_as_R(self, regression_dataset):
        """
        df <- read.csv('.../lifelines/datasets/regression.csv')
        coxph(formula=Surv(T, E) ~ var1 + var2, data=df, weights=var3)
        """
        df = regression_dataset

        cph = CoxPHFitter()
        with pytest.warns(StatisticalWarning, match="weights are not integers"):

            cph.fit(df, "T", "E", weights_col="var3", show_progress=True)
            expected = pd.Series({"var1": 0.3268, "var2": 0.0775})
            assert_series_equal(cph.params_, expected, atol=0.01, check_names=False)

            expected = pd.Series({"var1": 0.0697, "var2": 0.0861})
            assert_series_equal(cph.summary["se(coef)"], expected, atol=0.01, check_names=False)

    def test_summary_output_using_non_trivial_but_integer_weights(self, rossi):

        rossi_weights = rossi.copy()
        rossi_weights["weights"] = 1.0
        rossi_weights = rossi_weights.groupby(rossi.columns.tolist())["weights"].sum().reset_index()

        cf1 = CoxPHFitter()
        cf1.fit(rossi_weights, duration_col="week", event_col="arrest", weights_col="weights")

        cf2 = CoxPHFitter()
        cf2.fit(rossi, duration_col="week", event_col="arrest")

        # strictly speaking, the variances, etc. don't need to be the same, only the coefs.
        assert_frame_equal(cf1.summary, cf2.summary, check_like=True)

    def test_doubling_the_weights_halves_the_variance(self, rossi):

        w = 2.0
        rossi_weights = rossi.copy()
        rossi_weights["weights"] = 2

        cf1 = CoxPHFitter()
        cf1.fit(rossi_weights, duration_col="week", event_col="arrest", weights_col="weights")

        cf2 = CoxPHFitter()
        cf2.fit(rossi, duration_col="week", event_col="arrest")

        assert_series_equal(cf2.standard_errors_**2, w * cf1.standard_errors_**2)

    def test_adding_non_integer_weights_is_fine_if_robust_is_on(self, rossi):
        rossi["weights"] = np.random.exponential(1, rossi.shape[0])

        cox = CoxPHFitter()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            cox.fit(rossi, "week", "arrest", weights_col="weights", robust=True)

    def test_standard_error_coef_output_against_R(self, rossi):
        """
        from http://cran.r-project.org/doc/contrib/Fox-Companion/appendix-cox-regression.pdf
        Link is now broken, but this is the code:

        library(survival)
        rossi <- read.csv('.../lifelines/datasets/rossi.csv')
        mod.allison <- coxph(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio,
            data=rossi)
        summary(mod.allison)
        """
        expected = np.array([0.19138, 0.02200, 0.30799, 0.21222, 0.38187, 0.19576, 0.02865])
        cf = CoxPHFitter()
        cf.fit(rossi, duration_col="week", event_col="arrest")
        npt.assert_array_almost_equal(cf.summary["se(coef)"].values, expected, decimal=4)

    def test_z_value_output_against_R_to_3_decimal_places(self, rossi):
        """
        from http://cran.r-project.org/doc/contrib/Fox-Companion/appendix-cox-regression.pdf
        Link is now broken, but this is the code:

        library(survival)
        rossi <- read.csv('.../lifelines/datasets/rossi.csv')
        mod.allison <- coxph(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio,
            data=rossi)
        summary(mod.allison)
        """
        expected = np.array([-1.983, -2.611, 1.019, -0.706, -1.136, -0.434, 3.194])
        cf = CoxPHFitter()
        cf.fit(rossi, duration_col="week", event_col="arrest")
        npt.assert_array_almost_equal(cf.summary["z"].values, expected, decimal=3)

    def test_log_likelihood_test_against_R(self, rossi):
        """
        from http://cran.r-project.org/doc/contrib/Fox-Companion/appendix-cox-regression.pdf
        Link is now broken, but this is the code:

        library(survival)
        rossi <- read.csv('.../lifelines/datasets/rossi.csv')
        mod.allison <- coxph(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio,
            data=rossi)
        summary(mod.allison)
        """
        expected = 33.27
        cf = CoxPHFitter()
        cf.fit(rossi, duration_col="week", event_col="arrest")
        assert (cf.log_likelihood_ratio_test().test_statistic - expected) < 0.001

    def test_output_with_strata_against_R(self, rossi):
        """
        rossi <- read.csv('.../lifelines/datasets/rossi.csv')
        r = coxph(formula = Surv(week, arrest) ~ fin + age + strata(race,
                    paro, mar, wexp) + prio, data = rossi)
        """
        expected = np.array([-0.3355, -0.0590, 0.1002])
        cf = CoxPHFitter()
        cf.fit(rossi, duration_col="week", event_col="arrest", strata=["race", "paro", "mar", "wexp"], show_progress=True)
        npt.assert_array_almost_equal(cf.params_.values, expected, decimal=4)

    def test_penalized_output_against_R(self, rossi):
        # R code:
        #
        # rossi <- read.csv('.../lifelines/datasets/rossi.csv')
        # mod.allison <- coxph(Surv(week, arrest) ~ ridge(fin, age, race, wexp, mar, paro, prio,
        #                                                 theta=1.0, scale=TRUE), data=rossi)
        # cat(round(mod.allison$coefficients, 4), sep=", ")
        expected = np.array([-0.3761, -0.0565, 0.3099, -0.1532, -0.4295, -0.0837, 0.0909])
        cf = CoxPHFitter(penalizer=1.0 / rossi.shape[0])
        cf.fit(rossi, duration_col="week", event_col="arrest")
        npt.assert_array_almost_equal(cf.params_.values, expected, decimal=2)

    def test_coef_output_against_Survival_Analysis_by_John_Klein_and_Melvin_Moeschberger(self):
        # see example 8.3 in Survival Analysis by John P. Klein and Melvin L. Moeschberger, Second Edition
        df = load_kidney_transplant(usecols=["time", "death", "black_male", "white_male", "black_female"])
        cf = CoxPHFitter()
        cf.fit(df, duration_col="time", event_col="death")

        # coefs
        actual_coefs = cf.params_.values
        expected_coefs = np.array([0.1596, 0.2484, 0.6567])
        npt.assert_array_almost_equal(actual_coefs, expected_coefs, decimal=3)

    def test_se_against_Survival_Analysis_by_John_Klein_and_Melvin_Moeschberger(self):
        # see table 8.1 in Survival Analysis by John P. Klein and Melvin L. Moeschberger, Second Edition
        df = load_larynx()
        cf = CoxPHFitter()
        cf.fit(df, duration_col="time", event_col="death")

        # standard errors
        actual_se = cf._compute_standard_errors(None, None, None, None).values
        expected_se = np.array([0.0143, 0.4623, 0.3561, 0.4222])
        npt.assert_array_almost_equal(actual_se, expected_se, decimal=3)

    def test_p_value_against_Survival_Analysis_by_John_Klein_and_Melvin_Moeschberger(self):
        # see table 8.1 in Survival Analysis by John P. Klein and Melvin L. Moeschberger, Second Edition
        df = load_larynx()
        cf = CoxPHFitter()
        cf.fit(df, duration_col="time", event_col="death")

        # p-values
        actual_p = cf._compute_p_values()
        expected_p = np.array([0.1847, 0.7644, 0.0730, 0.00])
        npt.assert_array_almost_equal(actual_p, expected_p, decimal=2)

    def test_input_column_order_is_equal_to_output_hazards_order(self, rossi):
        cp = CoxPHFitter()
        expected = ["fin", "age", "race", "wexp", "mar", "paro", "prio"]
        cp.fit(rossi, event_col="week", duration_col="arrest")
        assert list(cp.params_.index.tolist()) == expected

    def test_strata_removes_variable_from_summary_output(self, rossi):
        cp = CoxPHFitter()
        cp.fit(rossi, "week", "arrest", strata=["race"])
        assert "race" not in cp.summary.index

    def test_strata_works_if_only_a_single_element_is_in_the_strata(self):
        df = load_holly_molly_polly()
        del df["Start(days)"]
        del df["Stop(days)"]
        del df["ID"]
        cp = CoxPHFitter()
        cp.fit(df, "T", "Status", strata=["Stratum"])
        assert True

    def test_coxph_throws_a_explainable_error_when_predict_sees_a_strata_it_hasnt_seen(self):
        training_df = pd.DataFrame.from_records(
            [
                {"t": 1, "e": 1, "s1": 0, "s2": 0, "v": 1.0},
                {"t": 2, "e": 1, "s1": 0, "s2": 0, "v": 1.5},
                {"t": 3, "e": 1, "s1": 0, "s2": 0, "v": 2.5},
                {"t": 3, "e": 1, "s1": 0, "s2": 1, "v": 2.5},
                {"t": 4, "e": 1, "s1": 0, "s2": 1, "v": 2.5},
                {"t": 3, "e": 1, "s1": 0, "s2": 1, "v": 4.5},
            ]
        )

        cp = CoxPHFitter()
        cp.fit(training_df, "t", "e", strata=["s1", "s2"])

        testing_df = pd.DataFrame.from_records(
            [
                {"t": 1, "e": 1, "s1": 1, "s2": 0, "v": 0.0},
                {"t": 2, "e": 1, "s1": 1, "s2": 0, "v": 0.5},
                {"t": 3, "e": 1, "s1": 1, "s2": 0, "v": -0.5},
            ]
        )

        with pytest.raises(StatError):
            cp.predict_median(testing_df)

    def test_strata_against_R_output(self, rossi):
        """
        > library(survival)
        > rossi = read.csv('.../lifelines/datasets/rossi.csv')
        > r = coxph(formula = Surv(week, arrest) ~ fin + age + strata(race,
            paro, mar, wexp) + prio, data = rossi)
        > r$loglik
        """

        cp = CoxPHFitter()
        cp.fit(rossi, "week", "arrest", strata=["race", "paro", "mar", "wexp"])

        npt.assert_almost_equal(cp.summary["coef"].values, [-0.335, -0.059, 0.100], decimal=3)
        assert abs(cp.log_likelihood_ - -436.9339) / 436.9339 < 0.01

    def test_baseline_hazard_works_with_strata_against_R_output(self, rossi):
        """
        > library(survival)
        > rossi = read.csv('.../lifelines/datasets/rossi.csv')
        > r = coxph(formula = Surv(week, arrest) ~ fin + age + strata(race,
            paro, mar, wexp) + prio, data = rossi)
        > basehaz(r, centered=TRUE)
        """
        cp = CoxPHFitter()
        cp.fit(rossi, "week", "arrest", strata=["race", "paro", "mar", "wexp"])
        npt.assert_almost_equal(
            cp.baseline_cumulative_hazard_[(0, 0, 0, 0)].loc[[14, 35, 37, 43, 52]].values,
            [0.076600555, 0.169748261, 0.272088807, 0.396562717, 0.396562717],
            decimal=4,
        )
        npt.assert_almost_equal(
            cp.baseline_cumulative_hazard_[(0, 0, 0, 1)].loc[[27, 43, 48, 52]].values,
            [0.095499001, 0.204196905, 0.338393113, 0.338393113],
            decimal=4,
        )

    def test_baseline_hazard_works_with_weights_against_R_output(self, rossi):
        """
        library(survival)

        fit<-coxph(Surv(week, arrest)~fin, data=rossi, weight=age)
        H0 <- basehaz(fit, centered=TRUE)
        """

        rossi = rossi[["week", "arrest", "fin", "age"]]
        cp = CoxPHFitter()
        cp.fit(rossi, "week", "arrest", weights_col="age")
        npt.assert_almost_equal(cp.baseline_cumulative_hazard_["baseline cumulative hazard"].loc[1.0], 0.00183466, decimal=4)
        npt.assert_almost_equal(cp.baseline_cumulative_hazard_["baseline cumulative hazard"].loc[2.0], 0.005880265, decimal=4)
        npt.assert_almost_equal(cp.baseline_cumulative_hazard_["baseline cumulative hazard"].loc[10.0], 0.035425868, decimal=4)
        npt.assert_almost_equal(cp.baseline_cumulative_hazard_["baseline cumulative hazard"].loc[52.0], 0.274341397, decimal=3)

    def test_strata_from_init_is_used_in_fit_later(self, rossi):
        strata = ["race", "paro", "mar"]
        cp_with_strata_in_init = CoxPHFitter(strata=strata)
        cp_with_strata_in_init.fit(rossi, "week", "arrest")
        assert cp_with_strata_in_init.strata == strata

        cp_with_strata_in_fit = CoxPHFitter()
        cp_with_strata_in_fit.fit(rossi, "week", "arrest", strata=strata)
        assert cp_with_strata_in_fit.strata == strata

        assert cp_with_strata_in_init.log_likelihood_ == cp_with_strata_in_fit.log_likelihood_

    def test_baseline_survival_is_the_same_indp_of_location(self, regression_dataset):
        df = regression_dataset.copy()
        cp1 = CoxPHFitter()
        cp1.fit(df, event_col="E", duration_col="T")

        df_demeaned = regression_dataset.copy()
        df_demeaned[["var1", "var2", "var3"]] = (
            df_demeaned[["var1", "var2", "var3"]] - df_demeaned[["var1", "var2", "var3"]].mean()
        )
        cp2 = CoxPHFitter()
        cp2.fit(df_demeaned, event_col="E", duration_col="T")
        assert_frame_equal(cp2.baseline_survival_, cp1.baseline_survival_)

    def test_baseline_cumulative_hazard_is_the_same_indp_of_location(self, regression_dataset):
        df = regression_dataset.copy()
        cp1 = CoxPHFitter()
        cp1.fit(df, event_col="E", duration_col="T")

        df_demeaned = regression_dataset.copy()
        df_demeaned[["var1", "var2", "var3"]] = (
            df_demeaned[["var1", "var2", "var3"]] - df_demeaned[["var1", "var2", "var3"]].mean()
        )
        cp2 = CoxPHFitter()
        cp2.fit(df_demeaned, event_col="E", duration_col="T")
        assert_frame_equal(cp2.baseline_cumulative_hazard_, cp1.baseline_cumulative_hazard_)

    def test_survival_prediction_is_the_same_indp_of_location(self, regression_dataset):
        df = regression_dataset.copy()

        df_demeaned = regression_dataset.copy()
        mean = df_demeaned[["var1", "var2", "var3"]].mean()
        df_demeaned[["var1", "var2", "var3"]] = df_demeaned[["var1", "var2", "var3"]] - mean

        cp1 = CoxPHFitter()
        cp1.fit(df, event_col="E", duration_col="T")

        cp2 = CoxPHFitter()
        cp2.fit(df_demeaned, event_col="E", duration_col="T")

        assert_frame_equal(
            cp1.predict_survival_function(df.iloc[[0]][["var1", "var2", "var3"]]),
            cp2.predict_survival_function(df_demeaned.iloc[[0]][["var1", "var2", "var3"]]),
        )

    def test_baseline_survival_is_the_same_indp_of_scale(self, regression_dataset):
        df = regression_dataset.copy()
        cp1 = CoxPHFitter()
        cp1.fit(df, event_col="E", duration_col="T")

        df_descaled = regression_dataset.copy()
        df_descaled[["var1", "var2", "var3"]] = (
            df_descaled[["var1", "var2", "var3"]] / df_descaled[["var1", "var2", "var3"]].std()
        )
        cp2 = CoxPHFitter()
        cp2.fit(df_descaled, event_col="E", duration_col="T")
        assert_frame_equal(cp2.baseline_survival_, cp1.baseline_survival_)

    def test_error_thrown_weights_are_nonpositive(self, regression_dataset):
        regression_dataset["weights"] = -1
        cph = CoxPHFitter()
        with pytest.raises(ValueError):
            cph.fit(regression_dataset, event_col="E", duration_col="T", weights_col="weights")

    def test_survival_prediction_is_the_same_indp_of_scale(self, regression_dataset):
        df = regression_dataset.copy()

        df_scaled = regression_dataset.copy()
        df_scaled[["var1", "var2", "var3"]] = df_scaled[["var1", "var2", "var3"]] * 10.0

        cp1 = CoxPHFitter()
        cp1.fit(df, event_col="E", duration_col="T")

        cp2 = CoxPHFitter()
        cp2.fit(df_scaled, event_col="E", duration_col="T")

        assert_frame_equal(
            cp1.predict_survival_function(df.iloc[[0]][["var1", "var2", "var3"]]),
            cp2.predict_survival_function(df_scaled.iloc[[0]][["var1", "var2", "var3"]]),
        )

    def test_warning_is_raised_if_df_has_a_near_constant_column(self, rossi):
        cox = CoxPHFitter()
        rossi["constant"] = 1.0

        with pytest.warns(ConvergenceWarning, match="variance") as w:
            with pytest.raises(ConvergenceError):
                cox.fit(rossi, "week", "arrest")

    def test_warning_is_raised_if_df_has_a_near_constant_column_in_one_separation(self, rossi):
        # check for a warning if we have complete separation
        cox = CoxPHFitter()
        ix = rossi["arrest"] == 1
        rossi.loc[ix, "paro"] = 1
        rossi.loc[~ix, "paro"] = 0

        with pytest.warns(ConvergenceWarning) as w:
            cox.fit(rossi, "week", "arrest")
            assert "complete separation" in str(w[0].message)
            assert "non-unique" in str(w[1].message)

    def test_warning_is_raised_if_complete_separation_is_present(self, cph):
        # check for a warning if we have complete separation

        df = pd.DataFrame.from_records(zip(np.arange(-5, 5), np.arange(1, 10)), columns=["x", "T"])
        with pytest.warns(ConvergenceWarning, match="complete separation") as w:
            cph.fit(df, "T")

        df = pd.DataFrame.from_records(zip(np.arange(1, 10), np.arange(1, 10)), columns=["x", "T"])
        with pytest.warns(ConvergenceWarning, match="complete separation") as w:
            cph.fit(df, "T")

        df = pd.DataFrame.from_records(zip(np.arange(0, 100), np.arange(0, 100)), columns=["x", "T"])
        df["x"] += 0.01 * np.random.randn(100)
        with pytest.warns(ConvergenceWarning, match="complete separation") as w:
            cph.fit(df, "T")

    def test_what_happens_when_column_is_constant_for_all_non_deaths(self, rossi):
        # this is known as complete separation: See https://stats.stackexchange.com/questions/11109/how-to-deal-with-perfect-separation-in-logistic-regression
        cp = CoxPHFitter()
        ix = rossi["arrest"] == 1
        rossi.loc[ix, "paro"] = 1

        with pytest.warns(ConvergenceWarning) as w:
            cp.fit(rossi, "week", "arrest", show_progress=True)

            assert cp.summary.loc["paro", "exp(coef)"] > 100

            assert "paro have very low variance" in w[0].message.args[0]
            assert "norm(delta)" in w[1].message.args[0]

    def test_what_happens_with_colinear_inputs(self, rossi, cph):
        with pytest.raises(ConvergenceError):
            rossi["duped"] = rossi["paro"] + rossi["prio"]
            cph.fit(rossi, "week", "arrest", show_progress=True)

    def test_durations_of_zero_are_okay(self, rossi, cph):
        rossi.loc[range(10), "week"] = 0
        cph.fit(rossi, "week", "arrest")

    def test_all_okay_with_non_trivial_index_in_dataframe(self, rossi):
        n = rossi.shape[0]

        cp1 = CoxPHFitter()
        cp1.fit(rossi, "week", event_col="arrest")

        cp2 = CoxPHFitter()
        rossi_new_index = rossi.set_index(np.random.randint(n, size=n))
        cp2.fit(rossi_new_index, "week", event_col="arrest")

        assert_frame_equal(cp2.summary, cp1.summary)

    def test_robust_errors_against_R_no_ties(self, regression_dataset, cph):
        df = regression_dataset
        cph.fit(df, "T", "E", robust=True)
        expected = pd.Series({"var1": 0.0879, "var2": 0.0847, "var3": 0.0655})
        assert_series_equal(cph.standard_errors_, expected, atol=0.01, check_names=False)

    def test_robust_errors_with_strata_against_R(self, rossi, cph):
        """
        df <- data.frame(
          "var1" = c(1, 1, 2, 2, 2, 1),
          "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092, 0.5),
          "var3" = c(1, 2, 3, 2, 1, 2),
          "T" = c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666, 8.)
        )
        df['E'] = 1

        coxph(formula=Surv(T, E) ~ strata(var1) + var2 + var3, data=df, robust=TRUE)
        """

        df = pd.DataFrame(
            {
                "var1": [1, 1, 2, 2, 2, 1],
                "var2": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092, 0.5],
                "var3": [1, 2, 3, 2, 1, 2],
                "T": [7.335846, 5.269797, 11.684092, 12.678458, 6.601666, 8.0],
            }
        )
        df["E"] = 1

        cph.fit(df, duration_col="T", event_col="E", strata=["var1"], robust=True)
        npt.assert_allclose(cph.summary["se(coef)"].values, np.array([1.076, 0.680]), rtol=1e-2)

    @pytest.mark.xfail
    def test_robust_errors_with_strata_against_R_super_accurate(self, rossi, cph):
        """
        df <- data.frame(
            "var1" = c(1, 1, 2, 2, 2),
            "var2" = c(0.184677, 0.071893, 1.364646, 0.098375, 1.663092),
            "T" = c( 7.335846, 5.269797, 11.684092, 12.678458, 6.601666)
        )
        df['E'] = 1

        coxph(formula=Surv(T, E) ~ strata(var1) + var2, data=df, robust=TRUE)
        """

        df = pd.DataFrame(
            {
                "var1": [1, 1, 2, 2, 2],
                "var2": [0.184677, 0.071893, 1.364646, 0.098375, 1.663092],
                "T": [7.335846, 5.269797, 11.684092, 12.678458, 6.601666],
            }
        )
        df["E"] = 1

        cph.fit(df, duration_col="T", event_col="E", strata=["var1"], robust=True)
        npt.assert_allclose(cph.summary["se(coef)"].values, 2.78649, rtol=1e-4)

    def test_what_happens_to_nans(self, rossi, cph):
        rossi["var4"] = np.nan
        with pytest.raises(TypeError):
            cph.fit(rossi, duration_col="week", event_col="arrest")

    def test_check_assumptions_fails_for_nonunique_index(self, cph, rossi):

        cph.fit(rossi, "week", "arrest")

        rossi.index = np.ones(rossi.shape[0])
        with pytest.raises(IndexError):
            cph.check_assumptions(rossi)


class TestAalenAdditiveFitter:
    @pytest.fixture()
    def aaf(self):
        return AalenAdditiveFitter()

    def test_can_accept_formula2(self):
        # https://github.com/CamDavidsonPilon/lifelines/issues/1314
        from lifelines.datasets import load_dd

        data = load_dd()
        data.head()

        aaf = AalenAdditiveFitter(coef_penalizer=1.0, fit_intercept=False)

        aaf.fit(data, "duration", event_col="observed", formula="un_continent_name + regime + start_year")

    def test_can_accept_formula(self, aaf, regression_dataset):
        aaf.fit(regression_dataset, "T", "E", formula="var1 * var2 + var3")
        assert aaf.summary.shape[0] == 5
        aaf.predict_survival_function(regression_dataset)

    def test_slope_tests_against_R(self, aaf, regression_dataset):
        """
        df['E'] = 1
        a = aareg(formula=Surv(T, E) ~ var1 + var2 + var3, data=df)
        plot(a)
        summary(a, test='nrisk')
        """
        regression_dataset["E"] = 1
        aaf.fit(regression_dataset, "T", "E")
        npt.assert_allclose(aaf.summary["slope(coef)"], [0.05141401, 0.01059746, 0.03923360, 0.07753566])

    def test_penalizer_reduces_norm_of_hazards(self, rossi):
        from numpy.linalg import norm

        aaf_without_penalizer = AalenAdditiveFitter(coef_penalizer=0.0, smoothing_penalizer=0.0)
        assert aaf_without_penalizer.coef_penalizer == aaf_without_penalizer.smoothing_penalizer == 0.0
        aaf_without_penalizer.fit(rossi, event_col="arrest", duration_col="week")

        aaf_with_penalizer = AalenAdditiveFitter(coef_penalizer=10.0, smoothing_penalizer=10.0)
        aaf_with_penalizer.fit(rossi, event_col="arrest", duration_col="week")
        assert norm(aaf_with_penalizer.cumulative_hazards_) <= norm(aaf_without_penalizer.cumulative_hazards_)

    def test_input_column_order_is_equal_to_output_hazards_order(self, rossi):
        aaf = AalenAdditiveFitter()
        expected = ["fin", "age", "race", "wexp", "mar", "paro", "prio"]
        aaf.fit(rossi, event_col="arrest", duration_col="week")
        assert list(aaf.cumulative_hazards_.columns.drop("Intercept")) == expected

        aaf = AalenAdditiveFitter(fit_intercept=False)
        expected = ["fin", "age", "race", "wexp", "mar", "paro", "prio"]
        aaf.fit(rossi, event_col="arrest", duration_col="week")
        assert list(aaf.cumulative_hazards_.columns) == expected

    def test_swapping_order_of_columns_in_a_df_is_okay(self, rossi):
        aaf = AalenAdditiveFitter()
        aaf.fit(rossi, event_col="arrest", duration_col="week")

        misorder = ["age", "race", "wexp", "mar", "paro", "prio", "fin"]
        natural_order = rossi.columns.drop(["week", "arrest"])
        deleted_order = rossi.columns.difference(["week", "arrest"])
        assert_series_equal(aaf.predict_median(rossi[natural_order]), aaf.predict_median(rossi[misorder]))
        assert_series_equal(aaf.predict_median(rossi[natural_order]), aaf.predict_median(rossi[deleted_order]))

        aaf = AalenAdditiveFitter(fit_intercept=False)
        aaf.fit(rossi, event_col="arrest", duration_col="week")
        assert_series_equal(aaf.predict_median(rossi[natural_order]), aaf.predict_median(rossi[misorder]))
        assert_series_equal(aaf.predict_median(rossi[natural_order]), aaf.predict_median(rossi[deleted_order]))

    def test_large_dimensions_for_recursion_error(self):
        n = 500
        d = 50
        X = pd.DataFrame(np.random.randn(n, d))
        T = np.random.exponential(size=n)
        X["T"] = T
        aaf = AalenAdditiveFitter(coef_penalizer=0.01)
        aaf.fit(X, duration_col="T")

    def test_aalen_additive_median_predictions_split_data(self):
        # This tests to make sure that my median predictions satisfy
        # the prediction are greater than the actual 1/2 the time.
        # generate some hazard rates and a survival data set
        n = 2500
        d = 5
        timeline = np.linspace(0, 70, 5000)
        hz, coef, X = generate_hazard_rates(n, d, timeline)
        T = generate_random_lifetimes(hz, timeline)

        X["T"] = T
        X = X.replace([np.inf, -np.inf], 10.0)
        # del X[5]

        # fit it to Aalen's model
        aaf = AalenAdditiveFitter(coef_penalizer=0.5, fit_intercept=False)
        aaf.fit(X, "T")

        # predictions
        T_pred = aaf.predict_median(X[list(range(6))])
        assert abs((T_pred.values > T).mean() - 0.5) < 0.05

    def test_dataframe_input_with_nonstandard_index(self):
        aaf = AalenAdditiveFitter(coef_penalizer=5.0)
        df = pd.DataFrame(
            [(16, True, True), (1, True, True), (4, False, True)],
            columns=["duration", "done_feeding", "white"],
            index=["a", "b", "c"],
        )
        aaf.fit(df, duration_col="duration", event_col="done_feeding")

    def test_crossval_for_aalen_add_concordance_index(self, data_pred2, data_pred1):
        aaf = AalenAdditiveFitter(coef_penalizer=0.1)
        for data_pred in [data_pred1, data_pred2]:
            mean_scores = []
            for repeat in range(20):
                scores = k_fold_cross_validation(
                    aaf, data_pred, duration_col="t", event_col="E", k=3, scoring_method="concordance_index"
                )
                mean_scores.append(np.mean(scores))

            expected = 0.90
            msg = "Expected min-mean c-index {:.2f} < {:.2f}"
            assert np.mean(mean_scores) > expected, msg.format(expected, np.mean(scores))

    @pytest.mark.xfail
    def test_crossval_for_aalen_add(self, data_pred2, data_pred1):
        aaf = AalenAdditiveFitter(coef_penalizer=0.1)
        for data_pred in [data_pred1, data_pred2]:
            mean_scores = []
            for repeat in range(20):
                scores = k_fold_cross_validation(
                    aaf, data_pred, duration_col="t", event_col="E", k=3, scoring_method="log_likelihood"
                )
                mean_scores.append(np.mean(scores))

            expected = 0.90
            msg = "Expected min-mean c-index {:.2f} < {:.2f}"
            assert np.mean(mean_scores) > expected, msg.format(expected, np.mean(scores))

    def test_predict_cumulative_hazard_inputs(self, data_pred1):
        aaf = AalenAdditiveFitter(coef_penalizer=0.001)
        aaf.fit(data_pred1, duration_col="t", event_col="E")
        x = data_pred1.iloc[:5].drop(["t", "E"], axis=1)
        y_df = aaf.predict_cumulative_hazard(x)
        # need to provide a intercept col
        x["int"] = 1.0
        y_np = aaf.predict_cumulative_hazard(x.values)
        assert_frame_equal(y_df, y_np)

    def test_aalen_additive_fitter_versus_R(self, aaf, rossi):
        """
        a = aareg(formula=Surv(week, arrest) ~ fin + age + race+ wexp + mar + paro + prio, data=head(rossi, 432))
        """
        aaf.fit(rossi, "week", "arrest")
        actual = aaf.hazards_
        npt.assert_allclose(actual.loc[:2, "fin"].tolist(), [-0.004628582, -0.005842295], rtol=1e-06)
        npt.assert_allclose(actual.loc[:2, "prio"].tolist(), [-1.268344e-03, 1.119377e-04], rtol=1e-06)
        npt.assert_allclose(actual.loc[:2, "Intercept"].tolist(), [1.913901e-02, -3.297233e-02], rtol=1e-06)

    def test_aalen_additive_fitter_versus_R_with_weights(self, aaf, regression_dataset):
        """
        df['E'] = 1
        a = aareg(formula=Surv(T, E) ~ var1 + var2, data=df, weights=var3)
        a$coefficient
        """
        regression_dataset["E"] = 1
        with pytest.warns(StatisticalWarning, match="weights are not integers"):
            aaf.fit(regression_dataset, "T", "E", weights_col="var3")
        actual = aaf.hazards_
        npt.assert_allclose(actual.iloc[:3]["var1"].tolist(), [1.301523e-02, -4.925302e-04, 2.304792e-02], rtol=1e-06)
        npt.assert_allclose(actual.iloc[:3]["Intercept"].tolist(), [-9.672957e-03, 1.439187e-03, 1.838915e-03], rtol=1e-06)

    def test_cumulative_hazards_versus_R(self, aaf, regression_dataset):
        """
        df['E'] = 1
        a = aareg(formula=Surv(T, E) ~ var1 + var2 + var3, data=df)
        c = a$coefficient
        apply(c, 2, cumsum)
        """
        regression_dataset["E"] = 1

        aaf.fit(regression_dataset, "T", "E")
        actual = aaf.cumulative_hazards_.iloc[-1]
        npt.assert_allclose(actual["Intercept"], 2.1675130235, rtol=1e-06)
        npt.assert_allclose(actual["var1"], 0.6820086125, rtol=1e-06)
        npt.assert_allclose(actual["var2"], -0.0776583514, rtol=1e-06)
        npt.assert_allclose(actual["var3"], 0.5515174017, rtol=1e-06)


class TestCoxTimeVaryingFitter:
    @pytest.fixture()
    def ctv(self):
        return CoxTimeVaryingFitter()

    @pytest.fixture()
    def dfcv(self):
        from lifelines.datasets import load_dfcv

        return load_dfcv()

    @pytest.fixture()
    def heart(self):
        return load_stanford_heart_transplants()

    def test_penalizer_can_be_an_array(self, dfcv):

        cph_array = CoxTimeVaryingFitter(penalizer=0.01 * np.ones(2)).fit(
            dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event"
        )
        cph_float = CoxTimeVaryingFitter(penalizer=0.01).fit(
            dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event"
        )

        assert_frame_equal(cph_array.summary, cph_float.summary)

    def test_penalizer_can_be_an_array_and_check_it_behaves_as_expected(self, dfcv):

        penalty = np.array([0, 0.01])
        cph_array = CoxTimeVaryingFitter(penalizer=penalty).fit(
            dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event"
        )
        cph_float = CoxTimeVaryingFitter(penalizer=0.01).fit(
            dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event"
        )

        assert abs(cph_array.summary.loc["z", "coef"]) > abs(cph_float.summary.loc["z", "coef"])

    def test_model_can_accept_null_covariates(self, ctv, dfcv):
        ctv.fit(dfcv[["id", "start", "stop", "event"]], id_col="id", start_col="start", stop_col="stop", event_col="event")

    def test_inference_against_known_R_output(self, ctv, dfcv):
        """
        from http://www.math.ucsd.edu/~rxu/math284/slect7.pdf

        > coxph(formula = Surv(time = start, time2 = stop, event) ~ group + z, data = dfcv)

        """
        ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event")
        npt.assert_almost_equal(ctv.summary["coef"].values, [1.826757, 0.705963], decimal=4)
        npt.assert_almost_equal(ctv.summary["se(coef)"].values, [1.229, 1.206], decimal=3)
        npt.assert_almost_equal(ctv.summary["p"].values, [0.14, 0.56], decimal=2)

    def test_that_id_col_is_optional(self, dfcv):

        ctv_with_id = CoxTimeVaryingFitter().fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event")
        ctv_without_id = CoxTimeVaryingFitter().fit(
            dfcv.drop("id", axis=1), start_col="start", stop_col="stop", event_col="event"
        )

        assert_frame_equal(ctv_without_id.summary, ctv_with_id.summary)

    def test_what_happens_to_nans(self, ctv, dfcv):
        """
        from http://www.math.ucsd.edu/~rxu/math284/slect7.pdf

        > coxph(formula = Surv(time = start, time2 = stop, event) ~ group + z, data = dfcv)

        """
        dfcv["var4"] = np.nan
        with pytest.raises(TypeError):
            ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event")

    def test_inference_against_known_R_output_with_weights(self, ctv, dfcv):
        """
        > dfcv['weights'] = [0.46009262, 0.04643257, 0.38150793, 0.11903676, 0.51965860, 0.96173133, 0.32435527, 0.16708398, 0.85464418, 0.15146481, 0.24713429, 0.55198318, 0.16948366, 0.19246483]
        > coxph(formula = Surv(time = start, time2 = stop, event) ~ group + z, data = dfcv)

        """
        dfcv["weights"] = [
            0.4600926178338619,
            0.046432574620396294,
            0.38150793079960477,
            0.11903675541025949,
            0.5196585971574837,
            0.9617313298681641,
            0.3243552664091651,
            0.16708398114269085,
            0.8546441798716636,
            0.15146480991643507,
            0.24713429350878657,
            0.5519831777187729,
            0.16948366380884838,
            0.19246482703103884,
        ]
        ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event", weights_col="weights")
        npt.assert_almost_equal(ctv.summary["coef"].values, [0.313, 0.423], decimal=3)
        npt.assert_almost_equal(ctv.summary["se(coef)"].values, [1.542, 1.997], decimal=3)

    def test_fitter_will_raise_an_error_if_immediate_death_present(self, ctv):
        df = pd.DataFrame.from_records(
            [
                {"id": 1, "start": 0, "stop": 0, "var": 1.0, "event": 1},
                {"id": 1, "start": 0, "stop": 10, "var": 2.0, "event": 1},
                {"id": 2, "start": 0, "stop": 10, "var": 3.0, "event": 1},
            ]
        )

        with pytest.raises(ValueError):
            ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")

    def test_fitter_will_raise_a_warning_if_instaneous_observation_present(self, ctv):
        df = pd.DataFrame.from_records(
            [
                {"id": 1, "start": 0, "stop": 0, "var": 1.0, "event": 0},  # note that start = stop here.
                {"id": 1, "start": 0, "stop": 10, "var": 1.0, "event": 1},
                {"id": 2, "start": 0, "stop": 10, "var": 2.0, "event": 1},
            ]
        )

        with pytest.warns(RuntimeWarning, match="safely dropped") as w:
            ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")

        df = df.loc[~((df["start"] == df["stop"]) & (df["start"] == 0))]

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")

    def test_fitter_will_error_if_degenerate_time(self, ctv):
        df = pd.DataFrame.from_records(
            [
                {"id": 1, "start": 0, "stop": 0, "event": 1},  # note the degenerate times
                {"id": 2, "start": 0, "stop": 5, "event": 1},
                {"id": 3, "start": 0, "stop": 5, "event": 1},
                {"id": 4, "start": 0, "stop": 4, "event": 1},
            ]
        )
        with pytest.raises(ValueError):
            ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")

        df.loc[(df["start"] == df["stop"]) & (df["start"] == 0) & df["event"], "stop"] = 0.5
        ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")
        assert True

    def test_ctv_fitter_will_handle_trivial_weight_col(self, ctv, dfcv):
        ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event")
        coefs_no_weights = ctv.summary["coef"].values

        dfcv["weight"] = 1.0
        ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event", weights_col="weight")
        coefs_trivial_weights = ctv.summary["coef"].values

        npt.assert_almost_equal(coefs_no_weights, coefs_trivial_weights, decimal=3)

    def test_doubling_the_weights_halves_the_variance(self, ctv, dfcv):
        ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event")
        coefs_no_weights = ctv.summary["coef"].values
        variance_no_weights = ctv.summary["se(coef)"].values ** 2

        dfcv["weight"] = 2.0
        ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event", weights_col="weight")
        coefs_double_weights = ctv.summary["coef"].values
        variance_double_weights = ctv.summary["se(coef)"].values ** 2

        npt.assert_almost_equal(coefs_no_weights, coefs_double_weights, decimal=3)
        npt.assert_almost_equal(variance_no_weights, 2 * variance_double_weights, decimal=3)

    def test_ctv_fitter_will_give_the_same_results_as_static_cox_model(self, ctv, rossi):

        cph = CoxPHFitter()
        cph.fit(rossi, "week", "arrest")
        expected = cph.params_.values

        rossi_ctv = rossi.reset_index()
        rossi_ctv = to_long_format(rossi_ctv, "week")

        ctv.fit(rossi_ctv, start_col="start", stop_col="stop", event_col="arrest", id_col="index")
        npt.assert_array_almost_equal(ctv.params_.values, expected, decimal=4)

    def test_ctv_fitter_will_handle_integer_weight_as_static_model(self, ctv, rossi):
        # deleting some columns to create more duplicates
        del rossi["age"]
        del rossi["paro"]
        del rossi["mar"]
        del rossi["prio"]

        rossi_ = rossi.copy()
        rossi_["weights"] = 1.0
        rossi_ = rossi_.groupby(rossi.columns.tolist())["weights"].sum().reset_index()

        cph = CoxPHFitter()
        cph.fit(rossi, "week", "arrest")
        expected = cph.params_.values

        # create the id column this way.
        rossi_ = rossi_.reset_index()
        rossi_ = to_long_format(rossi_, "week")

        ctv.fit(rossi_, start_col="start", stop_col="stop", event_col="arrest", id_col="index", weights_col="weights")
        npt.assert_array_almost_equal(ctv.params_.values, expected, decimal=3)

    def test_fitter_accept_boolean_columns(self, ctv):
        df = pd.DataFrame.from_records(
            [
                {"id": 1, "start": 0, "stop": 5, "var": -1.2, "bool": True, "event": 1},
                {"id": 2, "start": 0, "stop": 5, "var": 1.3, "bool": False, "event": 1},
                {"id": 3, "start": 0, "stop": 5, "var": -1.3, "bool": False, "event": 1},
            ]
        )

        ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")
        assert True

    def test_warning_is_raised_if_df_has_a_near_constant_column(self, ctv, dfcv):
        dfcv["constant"] = 1.0

        with pytest.warns(ConvergenceWarning, match="variance") as w:
            with pytest.raises(ConvergenceError):
                ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event")

    def test_warning_is_raised_if_df_has_a_near_constant_column_in_one_separation(self, ctv, dfcv):
        # check for a warning if we have complete separation
        ix = dfcv["event"]
        dfcv.loc[ix, "var3"] = 1
        dfcv.loc[~ix, "var3"] = 0

        with pytest.warns(ConvergenceWarning, match="complete separation") as w:
            ctv.fit(dfcv, id_col="id", start_col="start", stop_col="stop", event_col="event")

    def test_warning_is_raised_if_df_has_start_eq_stop_at_event_time(self, ctv):
        df = pd.DataFrame.from_records(
            [
                {"id": 1, "start": 0, "stop": 5, "event": 0},
                {"id": 1, "start": 5, "stop": 5, "event": 1},
                {"id": 2, "start": 0, "stop": 2, "event": 0},
                {"id": 2, "start": 2, "stop": 5, "event": 1},
                {"id": 3, "start": 0, "stop": 5, "event": 0},
                {"id": 3, "start": 6, "stop": 6, "event": 1},
            ]
        )

        with pytest.warns(ConvergenceWarning, match="with start and stop equal and a death event") as w:
            ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")

    def test_summary_output_versus_Rs_against_standford_heart_transplant(self, ctv, heart):
        """
        library(survival)
        data(heart)
        coxph(Surv(start, stop, event) ~ age + transplant + surgery + year, data= heart)
        """
        ctv.fit(heart, id_col="id", event_col="event")
        npt.assert_almost_equal(ctv.summary["coef"].values, [0.0272, -0.1463, -0.6372, -0.0103], decimal=3)
        npt.assert_almost_equal(ctv.summary["se(coef)"].values, [0.0137, 0.0705, 0.3672, 0.3138], decimal=3)
        npt.assert_almost_equal(ctv.summary["p"].values, [0.048, 0.038, 0.083, 0.974], decimal=3)

    def test_non_numeric_data_is_okay_with_formulas(self, ctv):
        ctv = CoxTimeVaryingFitter(penalizer=1.0)
        df = pd.DataFrame.from_dict(
            {
                "id": [1, 2, 3],
                "start": [0.0, 0.0, 0.0],
                "end": [1.0, 2.0, 3.0],
                "e": [1, 1, 1],
                "bool_": [True, True, False],
                "int_": [1, -1, 0],
                "uint8_": pd.Series([1, 3, 0], dtype="uint8"),
                "string_": ["test", "a", "2.5"],
                "float_": [1.2, -0.5, 0.0],
                "categorya_": pd.Series([1, 2, 3], dtype="category"),
                "categoryb_": pd.Series(["a", "b", "a"], dtype="category"),
            }
        )

        for subset in [
            ["start", "end", "e", "id", "categoryb_"],
            ["start", "end", "e", "id", "string_"],
            ["start", "end", "e", "id", "categorya_"],
            ["start", "end", "e", "id", "bool_"],
            ["start", "end", "e", "id", "int_"],
            ["start", "end", "e", "id", "float_"],
            ["start", "end", "e", "id", "uint8_"],
        ]:
            df_ = df[subset]
            formula = subset[-1]
            ctv.fit(df_, id_col="id", event_col="e", stop_col="end", formula=formula)

    def test_ctv_prediction_methods(self, ctv, heart):
        ctv.fit(heart, id_col="id", event_col="event")
        assert ctv.predict_log_partial_hazard(heart).shape[0] == heart.shape[0]
        assert ctv.predict_partial_hazard(heart).shape[0] == heart.shape[0]

    def test_ctv_baseline_cumulative_hazard_against_R(self, ctv, heart):
        """
        library(survival)
        data(heart)
        r = coxph(Surv(start, stop, event) ~ age + transplant + surgery + year, data=heart)

        sest = survfit(r, se.fit = F)
        sest$cumhaz
        """
        expected = [
            0.008576073,
            0.034766771,
            0.061749725,
            0.080302426,
            0.09929016,
            0.109040953,
            0.118986351,
            0.129150022,
            0.160562122,
            0.171388794,
            0.182287871,
            0.204408269,
            0.215630422,
            0.227109569,
            0.238852428,
            0.250765502,
            0.26291466,
            0.275185886,
            0.287814114,
            0.313833224,
            0.327131062,
            0.340816277,
            0.354672739,
            0.368767829,
            0.383148661,
            0.397832317,
            0.412847777,
            0.428152773,
            0.459970612,
            0.476275941,
            0.50977267,
            0.52716976,
            0.545297536,
            0.563803467,
            0.582672943,
            0.602305488,
            0.622619844,
            0.643438746,
            0.664737826,
            0.686688715,
            0.7093598,
            0.732698614,
            0.756553038,
            0.781435099,
            0.806850698,
            0.832604447,
            0.859118436,
            0.886325942,
            0.914877455,
            0.975077858,
            1.006355139,
            1.039447234,
            1.073414895,
            1.109428518,
            1.155787187,
            1.209776781,
            1.26991066,
            1.3421101,
            1.431890995,
            1.526763781,
            1.627902989,
            1.763620039,
        ]
        ctv.fit(heart, id_col="id", event_col="event")
        npt.assert_array_almost_equal(ctv.baseline_cumulative_hazard_.values[0:3, 0], expected[0:3], decimal=3)
        npt.assert_array_almost_equal(
            ctv.baseline_cumulative_hazard_.values[:, 0], expected, decimal=2
        )  # errors accumulate fast =(

    def test_repr_with_fitter(self, ctv, heart):
        ctv.fit(heart, id_col="id", event_col="event")
        uniques = heart["id"].unique().shape[0]
        assert ctv.__repr__() == "<lifelines.CoxTimeVaryingFitter: fitted with %d periods, %d subjects, %d events>" % (
            heart.shape[0],
            uniques,
            heart["event"].sum(),
        )

    def test_all_okay_with_non_trivial_index_in_dataframe(self, ctv, heart):
        n = heart.shape[0]

        ctv1 = CoxTimeVaryingFitter()
        ctv1.fit(heart, id_col="id", event_col="event")

        ctv2 = CoxTimeVaryingFitter()
        heart_new_index = heart.set_index(np.random.randint(n, size=n))
        ctv2.fit(heart_new_index, id_col="id", event_col="event")

        assert_frame_equal(ctv2.summary, ctv1.summary)

    def test_penalizer(self, heart):
        ctv = CoxTimeVaryingFitter(penalizer=1.0)
        ctv.fit(heart, id_col="id", event_col="event")
        assert True

    def test_likelihood_ratio_test_against_R(self, ctv, heart):
        ctv.fit(heart, id_col="id", event_col="event")
        sr = ctv.log_likelihood_ratio_test()
        test_stat, deg_of_freedom, p_value = sr.test_statistic, sr.degrees_freedom, sr.p_value
        assert abs(test_stat - 15.1) < 0.1
        assert abs(p_value - 0.00448) < 0.001
        assert deg_of_freedom == 4

    def test_error_thrown_weights_are_nonpositive(self, ctv, heart):
        heart["weights"] = -1
        with pytest.raises(ValueError):
            ctv.fit(heart, id_col="id", event_col="event", weights_col="weights")

    def test_error_thrown_if_column_doesnt_exist(self, ctv, heart):
        with pytest.raises(KeyError):
            ctv.fit(heart, id_col="_id_", event_col="event")

    def test_print_summary(self, ctv, heart):
        ctv.fit(heart, id_col="id", event_col="event")

        import sys

        saved_stdout = sys.stdout
        try:
            out = StringIO()
            sys.stdout = out

            ctv.fit(heart, id_col="id", event_col="event")
            ctv._time_fit_was_called = "2018-10-23 02:41:45 UTC"
            ctv.print_summary()
            output = out.getvalue().strip().split()
            expected = (
                (
                    repr(ctv)
                    + "\n"
                    + """
<lifelines.CoxTimeVaryingFitter: fitted with 172 periods, 103 subjects, 75 events>
         event col = 'event'
number of subjects = 103
 number of periods = 172
  number of events = 75
partial log-likelihood = -290.57
  time fit was run = 2018-10-23 02:41:45 UTC

---
             coef  exp(coef)   se(coef)   coef lower 95%   coef upper 95%  exp(coef) lower 95%  exp(coef) upper 95%
age          0.03       1.03       0.01             0.00             0.05                 1.00                 1.06
year        -0.15       0.86       0.07            -0.28            -0.01                 0.75                 0.99
surgery     -0.64       0.53       0.37            -1.36             0.08                 0.26                 1.09
transplant  -0.01       0.99       0.31            -0.63             0.60                 0.54                 1.83

               z    p   -log2(p)
age         1.98 0.05       4.39
year       -2.08 0.04       4.72
surgery    -1.74 0.08       3.60
transplant -0.03 0.97       0.04
---
Partial AIC = 589.13
log-likelihood ratio test = 15.11 on 4 df
-log2(p) of ll-ratio test = 7.80
"""
                )
                .strip()
                .split()
            )
            for i in [0, 1, 2, 3, -2, -1, -3, -4, -5]:
                assert output[i] == expected[i]
        finally:
            sys.stdout = saved_stdout

    def test_ctv_against_cph_for_static_datasets_but_one_is_long(self):
        rossi = load_rossi()
        long_rossi = to_episodic_format(rossi, "week", "arrest")
        assert rossi.shape[0] < long_rossi.shape[0]

        ctv = CoxTimeVaryingFitter()
        ctv.fit(long_rossi, id_col="id", event_col="arrest")

        cph = CoxPHFitter()
        cph.fit(rossi, "week", "arrest")

        assert_frame_equal(cph.summary, ctv.summary, check_like=True, atol=0.001)

    def test_ctv_with_strata_against_R(self, ctv, heart):
        """
        library(survival)
        data(heart)
        r = coxph(Surv(start, stop, event) ~ age + strata(transplant) + surgery + year, data=heart)
        r
        logLik(r)
        """
        ctv.fit(heart, id_col="id", event_col="event", strata="transplant")
        summary = ctv.summary.sort_index()
        npt.assert_allclose(summary["coef"].tolist(), [0.0293, -0.6176, -0.1527], atol=0.001)
        npt.assert_allclose(summary["se(coef)"].tolist(), [0.0139, 0.3707, 0.0710], atol=0.001)
        npt.assert_allclose(summary["z"].tolist(), [2.11, -1.67, -2.15], atol=0.01)
        npt.assert_allclose(ctv.log_likelihood_, -254.7144, atol=0.01)

    def test_ctv_with_multiple_strata(self, ctv, heart):
        ctv.fit(heart, id_col="id", event_col="event", strata=["transplant", "surgery"])
        npt.assert_allclose(ctv.log_likelihood_, -230.6726, atol=0.01)

    def test_ctv_ratio_test_with_strata(self, ctv, heart):
        ctv.fit(heart, id_col="id", event_col="event", strata=["transplant"])
        npt.assert_allclose(ctv.log_likelihood_ratio_test().test_statistic, 15.68, atol=0.01)

    def test_ctv_ratio_test_with_strata_and_initial_point(self, ctv, heart):
        ctv.fit(heart, id_col="id", event_col="event", strata=["transplant"], initial_point=0.1 * np.ones(3))
        npt.assert_allclose(ctv.log_likelihood_ratio_test().test_statistic, 15.68, atol=0.01)

    def test_fitter_is_okay_with_trival_df(self, ctv):
        # after all the necessary columns are removed, does this fitter still work with a trivial df?
        df = pd.DataFrame.from_records(
            [
                {"id": 1, "start": 0, "stop": 4, "event": 1},
                {"id": 2, "start": 0, "stop": 5, "event": 1},
                {"id": 3, "start": 0, "stop": 5, "event": 1},
                {"id": 4, "start": 0, "stop": 4, "event": 1},
            ]
        )
        ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")

        assert True


class TestAalenJohansenFitter:
    @pytest.fixture  # pytest fixtures are functions that are "executed" before every test
    def duration(self):
        return [1, 2, 3, 4, 5, 6]

    @pytest.fixture
    def event_observed(self):
        return [0, 1, 1, 2, 2, 0]

    @pytest.fixture
    def fitter(self):
        return AalenJohansenFitter()

    @pytest.fixture
    def kmfitter(self):
        return KaplanMeierFitter()

    def test_jitter(self, fitter):
        d = pd.Series([1, 1, 1])
        e = fitter._jitter(durations=d, event=pd.Series([1, 1, 1]), jitter_level=0.01)

        npt.assert_equal(np.any(np.not_equal(d, e)), True)

    def test_tied_input_data(self, fitter):
        # Based on new setup of ties, this counts as a valid tie
        d = [1, 2, 2, 4, 5, 6]
        with pytest.warns(Warning, match="Tied event times"):
            fitter.fit(durations=d, event_observed=[0, 1, 2, 1, 2, 0], event_of_interest=2)
            npt.assert_equal(np.any(np.not_equal([0] + d, fitter.event_table.index)), True)

    def test_updated_input_ties(self, fitter):
        # Based on the new setup of ties, should not detect any ties as existing
        d = [1, 2, 2, 4, 5, 6]
        fitter.fit(durations=d, event_observed=[0, 1, 1, 1, 2, 0], event_of_interest=1)
        npt.assert_equal(np.asarray([0, 1, 2, 4, 5, 6]), np.asarray(fitter.event_table.index))

    def test_updated_censor_ties(self, fitter):
        # Based on the new setup of ties, should not detect any ties as existing
        d = [1, 2, 2, 4, 5, 6]
        fitter.fit(durations=d, event_observed=[0, 0, 1, 1, 2, 0], event_of_interest=1)
        npt.assert_equal(np.asarray([0, 1, 2, 4, 5, 6]), np.asarray(fitter.event_table.index))

    def test_event_table_is_correct(self, fitter, duration, event_observed):
        fitter.fit(duration, event_observed, event_of_interest=2)

        expected_event_table = pd.DataFrame.from_records(
            [
                {"event_at": 0.0, "removed": 0, "observed": 0, "observed_2": 0, "censored": 0, "entrance": 6, "at_risk": 6},
                {"event_at": 1.0, "removed": 1, "observed": 0, "observed_2": 0, "censored": 1, "entrance": 0, "at_risk": 6},
                {"event_at": 2.0, "removed": 1, "observed": 1, "observed_2": 0, "censored": 0, "entrance": 0, "at_risk": 5},
                {"event_at": 3.0, "removed": 1, "observed": 1, "observed_2": 0, "censored": 0, "entrance": 0, "at_risk": 4},
                {"event_at": 4.0, "removed": 1, "observed": 1, "observed_2": 1, "censored": 0, "entrance": 0, "at_risk": 3},
                {"event_at": 5.0, "removed": 1, "observed": 1, "observed_2": 1, "censored": 0, "entrance": 0, "at_risk": 2},
                {"event_at": 6.0, "removed": 1, "observed": 0, "observed_2": 0, "censored": 1, "entrance": 0, "at_risk": 1},
            ]
        ).set_index("event_at")[["removed", "observed", "observed_2", "censored", "entrance", "at_risk"]]
        # pandas util for checking if two dataframes are equal
        assert_frame_equal(
            fitter.event_table, expected_event_table, check_dtype=False, check_like=True
        )  # Ignores dtype to avoid int32 vs int64 difference

    def test_aj_less_than_km(self, fitter, kmfitter, duration, event_observed):
        # In presence of competing risk, CIF_{AJ} >= CIF_{KM}
        fitter.fit(duration, event_observed, event_of_interest=2)  # Aalen-Johansen
        kmfitter.fit(duration, event_observed)

        x = np.all(np.where(np.array(1 - kmfitter.survival_function_) >= np.array(fitter.cumulative_density_), True, False))
        assert x

    def test_no_competing_risk(self, fitter, kmfitter, duration):
        # In presence of no competing risk, CIF_{AJ} == CIF_{KM}
        same_events = [0, 2, 2, 2, 2, 0]
        fitter.fit(duration, same_events, event_of_interest=2)  # Aalen-Johansen
        kmfitter.fit(duration, same_events)  # Kaplan-Meier
        npt.assert_allclose(np.array(1 - kmfitter.survival_function_), np.array(fitter.cumulative_density_))

    def test_variance_calculation_against_sas(self, fitter, duration, event_observed):
        variance_from_sas = np.array([0.0, 0.0, 0.0, 0.0, 0.032, 0.048, 0.048])

        fitter.fit(duration, event_observed, event_of_interest=2)
        npt.assert_allclose(variance_from_sas, np.array(fitter.variance_))

    def test_ci_calculation_against_sas(self, fitter, duration, event_observed):
        ci_from_sas = np.array(
            [
                [np.nan, np.nan],
                [np.nan, np.nan],
                [np.nan, np.nan],
                [np.nan, np.nan],
                [0.00836904, 0.58185303],
                [0.05197575, 0.75281579],
                [0.05197575, 0.75281579],
            ]
        )

        fitter.fit(duration, event_observed, event_of_interest=2)
        npt.assert_allclose(ci_from_sas, np.array(fitter.confidence_interval_))


class TestMixtureCureFitter:
    @flaky
    def test_exponential_data_produces_correct_inference_for_both_cure_and_non_cure_fractions(self):
        N = 1000000
        scale = 5
        T = np.random.exponential(scale, size=N)
        observed = np.ones(N, dtype=bool)

        # Censor the data at time = 8
        last_observation_time = 8.0
        mask = T > last_observation_time
        T[mask] = last_observation_time
        observed[mask] = False

        # Add in some 'cured' samples, to make it 20% cured
        C = int(N / 4)
        T = np.concatenate([T, last_observation_time * np.ones(C)])
        observed = np.concatenate([observed, np.zeros(C, dtype=bool)])

        fitter = MixtureCureFitter(base_fitter=ExponentialFitter())
        fitter.fit(T, event_observed=observed)
        assert abs(fitter.cured_fraction_ - 0.2) < 0.01
        assert abs(fitter.lambda_ / scale - 1) < 0.01
        assert abs(fitter._survival_function([0.2, 1], 1) - 0.49430) < 0.01
        assert abs(fitter.percentile(0.6) - scale * np.log(2)) < 0.01

        assert fitter.percentile(0.19) is np.inf

    def test_should_raise_exception_if_cure_parameter_is_already_in_list_of_parameter_names(self):
        with pytest.raises(
            NameError,
            match="'cured_fraction_' in _fitted_parameter_names is a lifelines reserved word." " Try something else instead.",
        ):
            MixtureCureFitter(MixtureCureFitter(base_fitter=ExponentialFitter()))

    def test_should_get_same_values_as_custom_weibull_on_kidney_transplant_data_set(self):
        class WeibullMixtureCureFitter(ParametricUnivariateFitter):
            _fitted_parameter_names = ["c_", "lambda_", "rho_"]
            _bounds = [(0, 1), (0, None), (0, None)]
            _compare_to_values = anp.array([0.0, 1.0, 1.0])

            def _cumulative_hazard(self, params, times):
                c_, lambda_, rho_ = params
                weibull_survival_function = anp.exp(-((times / lambda_) ** rho_))
                return -anp.log(c_ + (1 - c_) * weibull_survival_function)

            def _create_initial_point(self, Ts, E, entry, weights):
                return anp.array([0.1, 1.0, 1.0])

        wmc = WeibullMixtureCureFitter()
        mcfitter = MixtureCureFitter(base_fitter=WeibullFitter())

        T, E = load_kidney_transplant()["time"], load_kidney_transplant()["death"]
        wmc.fit(T, E)
        mcfitter.fit(T, E)
        print(wmc.summary)
        print(mcfitter.summary)
        assert_frame_equal(wmc.summary.reset_index(drop=True), mcfitter.summary.reset_index(drop=True), rtol=0.25)
