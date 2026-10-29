// Moduł Pythona `quantpulse` - cienka warstwa nad nagłówkami z include/quantpulse.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include "quantpulse/rolling.hpp"

namespace py = pybind11;
using namespace quantpulse;

namespace {

using Arr = py::array_t<double, py::array::c_style | py::array::forcecast>;

template <class Fn>
py::array_t<double> apply(const Arr& x, Fn fn) {
    if (x.ndim() != 1) throw std::invalid_argument("oczekuję tablicy jednowymiarowej");
    const double* data = x.data();
    auto n = static_cast<std::size_t>(x.shape(0));
    std::vector<double> out;
    {
        py::gil_scoped_release release;   // liczenie bez blokady Pythona - pozwala na wątki
        out = fn(data, n);
    }
    return py::array_t<double>(static_cast<py::ssize_t>(out.size()), out.data());
}

void check_window(std::size_t window) {
    if (window == 0) throw std::invalid_argument("window musi być > 0");
}

}  // namespace

PYBIND11_MODULE(_core, m) {
    m.doc() = "QuantPulse - szybkie obliczenia dla laboratorium HossaLab";

    py::class_<RollingWindow>(m, "RollingWindow",
                              "Statystyki w przesuwanym oknie liczone w locie (Welford).")
        .def(py::init<std::size_t>(), py::arg("window"))
        .def("push", &RollingWindow::push, py::arg("x"))
        .def("reset", &RollingWindow::reset)
        .def("mean", &RollingWindow::mean)
        .def("var", &RollingWindow::var, py::arg("ddof") = 1)
        .def("std", &RollingWindow::stddev, py::arg("ddof") = 1)
        .def("zscore", &RollingWindow::zscore, py::arg("x"), py::arg("ddof") = 1)
        .def("at_lag", &RollingWindow::at_lag, py::arg("lag"))
        .def_property_readonly("window", &RollingWindow::window)
        .def_property_readonly("size", &RollingWindow::size)
        .def_property_readonly("full", &RollingWindow::full)
        .def_property_readonly("ready", &RollingWindow::ready)
        .def("__len__", &RollingWindow::size);

    m.def("rolling_mean", [](const Arr& x, std::size_t window) {
        check_window(window);
        return apply(x, [&](const double* d, std::size_t n) { return rolling_mean(d, n, window); });
    }, py::arg("x"), py::arg("window"), "Średnia krocząca; pierwsze window-1 wyników = NaN.");

    m.def("rolling_std", [](const Arr& x, std::size_t window, int ddof) {
        check_window(window);
        return apply(x, [&](const double* d, std::size_t n) { return rolling_std(d, n, window, ddof); });
    }, py::arg("x"), py::arg("window"), py::arg("ddof") = 1, "Odchylenie standardowe kroczące.");

    m.def("rolling_zscore", [](const Arr& x, std::size_t window, int ddof) {
        check_window(window);
        return apply(x, [&](const double* d, std::size_t n) { return rolling_zscore(d, n, window, ddof); });
    }, py::arg("x"), py::arg("window"), py::arg("ddof") = 1, "Z-score wartości względem okna.");

#ifdef QUANTPULSE_VERSION
    m.attr("__version__") = QUANTPULSE_VERSION;
#endif
}
