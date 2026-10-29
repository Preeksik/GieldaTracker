#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <random>
#include <vector>

#include "quantpulse/rolling.hpp"

using quantpulse::RollingWindow;

namespace {

// Wzorzec: liczenie wprost, dwuprzebiegowo.
void naive(const std::vector<double>& x, std::size_t end, std::size_t w, double& mean, double& var) {
    double s = 0;
    for (std::size_t i = end + 1 - w; i <= end; ++i) s += x[i];
    mean = s / static_cast<double>(w);
    double m2 = 0;
    for (std::size_t i = end + 1 - w; i <= end; ++i) m2 += (x[i] - mean) * (x[i] - mean);
    var = m2 / static_cast<double>(w - 1);
}

}  // namespace

TEST(RollingWindow, NotReadyUntilFull) {
    RollingWindow w(3);
    w.push(1.0);
    w.push(2.0);
    EXPECT_FALSE(w.ready());
    EXPECT_TRUE(std::isnan(w.mean()));
    w.push(3.0);
    EXPECT_TRUE(w.ready());
    EXPECT_DOUBLE_EQ(w.mean(), 2.0);
    EXPECT_DOUBLE_EQ(w.var(), 1.0);
    EXPECT_DOUBLE_EQ(w.var(0), 2.0 / 3.0);
}

TEST(RollingWindow, SlidesAndKeepsLags) {
    RollingWindow w(3);
    for (double x : {1.0, 2.0, 3.0, 10.0}) w.push(x);
    EXPECT_DOUBLE_EQ(w.mean(), 5.0);       // 2, 3, 10
    EXPECT_DOUBLE_EQ(w.at_lag(0), 10.0);
    EXPECT_DOUBLE_EQ(w.at_lag(2), 2.0);
    EXPECT_TRUE(std::isnan(w.at_lag(3)));
}

TEST(RollingWindow, NanBlocksWindowUntilItLeaves) {
    RollingWindow w(3);
    const double nan = std::numeric_limits<double>::quiet_NaN();
    for (double x : {1.0, nan, 3.0}) w.push(x);
    EXPECT_TRUE(std::isnan(w.mean()));
    w.push(4.0);                           // NaN wciąż w oknie
    EXPECT_TRUE(std::isnan(w.mean()));
    w.push(5.0);                           // 3, 4, 5
    EXPECT_DOUBLE_EQ(w.mean(), 4.0);
}

TEST(RollingWindow, MatchesNaiveOverLongSeries) {
    std::mt19937_64 rng(42);
    std::normal_distribution<double> d(100.0, 5.0);
    const std::size_t n = 50000, win = 20;
    std::vector<double> x(n);
    for (auto& v : x) v = d(rng);
    RollingWindow w(win);
    double worst = 0;
    for (std::size_t i = 0; i < n; ++i) {
        w.push(x[i]);
        if (i + 1 >= win) {
            double m, v;
            naive(x, i, win, m, v);
            worst = std::max({worst, std::abs(w.mean() - m), std::abs(w.var() - v) / v});
        }
    }
    EXPECT_LT(worst, 1e-9);
}

TEST(RollingWindow, ConstantSeriesHasZeroStd) {
    RollingWindow w(5);
    for (int i = 0; i < 100; ++i) w.push(0.1);
    EXPECT_GE(w.var(), 0.0);
    EXPECT_NEAR(w.stddev(), 0.0, 1e-12);
    EXPECT_TRUE(std::isnan(w.zscore(0.1)));   // dzielenie przez zero -> NaN, nie inf
}
