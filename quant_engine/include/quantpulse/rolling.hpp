// QuantPulse - statystyki w przesuwanym oknie, liczone w locie (O(1) na nową wartość).
//
// Średnia i wariancja metodą Welforda: dodanie nowej wartości i usunięcie najstarszej
// aktualizuje wynik bez przeliczania całego okna. Usuwanie to odejmowanie, więc błędy
// zaokrągleń zostają w sumie kwadratów (m2) nawet gdy zmienność potem spada - np. po
// gwałtownym ruchu cena stoi w miejscu. Dlatego klasa szacuje swój błąd (err_) i gdy
// przekroczy on kRelTol * m2, przelicza okno od zera. Dodatkowo co kResyncEvery kroków
// przelicza średnią. Wynik zgadza się z liczeniem wprost do ~1e-10 (względnie).
//
// Brakujące wartości (NaN): nie wchodzą do statystyk, ale okno, w którym jest choć jeden NaN,
// zwraca NaN - tak samo jak pandas `rolling(window).mean()` z domyślnym min_periods=window.
#pragma once

#include <cmath>
#include <cstddef>
#include <limits>
#include <stdexcept>
#include <vector>

namespace quantpulse {

class RollingWindow {
public:
    explicit RollingWindow(std::size_t window) : window_(window), buf_(window) {
        if (window == 0) throw std::invalid_argument("window musi być > 0");
    }

    // Dodaje wartość; gdy okno jest pełne, najstarsza wypada.
    void push(double x) {
        if (size_ == window_) {
            pop_oldest();
        }
        buf_[(head_ + size_) % window_] = x;
        ++size_;
        if (std::isnan(x)) {
            ++nan_count_;
        } else {
            add(x);
        }
        if (++since_resync_ >= kResyncEvery || err_ > kRelTol * m2_) resync();
    }

    void reset() {
        head_ = size_ = n_ = nan_count_ = since_resync_ = 0;
        mean_ = m2_ = err_ = 0.0;
    }

    std::size_t window() const { return window_; }
    std::size_t size() const { return size_; }
    bool full() const { return size_ == window_; }

    // Czy okno daje wynik: pełne i bez braków.
    bool ready() const { return full() && nan_count_ == 0; }

    double mean() const { return ready() ? mean_ : kNaN; }

    // ddof = 1: wariancja z próby (jak pandas), ddof = 0: populacyjna (jak numpy domyślnie).
    double var(int ddof = 1) const {
        if (!ready() || static_cast<long long>(n_) - ddof <= 0) return kNaN;
        double v = m2_ / static_cast<double>(n_ - ddof);
        return v < 0.0 ? 0.0 : v;   // zabezpieczenie przed -1e-18 z zaokrągleń
    }

    // (nazwa stddev, nie std - żeby nie mylić z przestrzenią nazw std:: w żadnym kompilatorze)
    double stddev(int ddof = 1) const {
        double v = var(ddof);
        return std::isnan(v) ? kNaN : std::sqrt(v);
    }

    // Ile odchyleń standardowych od średniej okna jest wartość x.
    double zscore(double x, int ddof = 1) const {
        double s = stddev(ddof);
        if (std::isnan(s) || s == 0.0) return kNaN;
        return (x - mean_) / s;
    }

    // Wartość sprzed `lag` kroków (0 = ostatnia dodana).
    double at_lag(std::size_t lag) const {
        if (lag >= size_) return kNaN;
        return buf_[(head_ + size_ - 1 - lag) % window_];
    }

    static constexpr std::size_t kResyncEvery = 4096;
    static constexpr double kRelTol = 1e-11;

private:
    static constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
    static constexpr double kEps = std::numeric_limits<double>::epsilon();

    // Górne oszacowanie błędu jednej aktualizacji m2.
    void track_error(double term) { err_ += 4.0 * kEps * (std::abs(m2_) + std::abs(term)); }

    void add(double x) {
        ++n_;
        double d = x - mean_;
        mean_ += d / static_cast<double>(n_);
        double term = d * (x - mean_);
        m2_ += term;
        track_error(term);
    }

    void remove(double x) {
        if (n_ <= 1) {
            n_ = 0;
            mean_ = m2_ = err_ = 0.0;
            return;
        }
        double old_mean = mean_;
        --n_;
        mean_ = old_mean - (x - old_mean) / static_cast<double>(n_);
        double term = (x - old_mean) * (x - mean_);
        m2_ -= term;
        track_error(term);
    }

    void pop_oldest() {
        double x = buf_[head_];
        head_ = (head_ + 1) % window_;
        --size_;
        if (std::isnan(x)) {
            --nan_count_;
        } else {
            remove(x);
        }
    }

    // Przeliczenie od zera (dwuprzebiegowo - najdokładniej).
    void resync() {
        since_resync_ = 0;
        err_ = 0.0;
        n_ = 0;
        double sum = 0.0;
        for (std::size_t i = 0; i < size_; ++i) {
            double x = buf_[(head_ + i) % window_];
            if (!std::isnan(x)) { sum += x; ++n_; }
        }
        mean_ = n_ ? sum / static_cast<double>(n_) : 0.0;
        m2_ = 0.0;
        for (std::size_t i = 0; i < size_; ++i) {
            double x = buf_[(head_ + i) % window_];
            if (!std::isnan(x)) { double d = x - mean_; m2_ += d * d; }
        }
    }

    std::size_t window_;
    std::vector<double> buf_;
    std::size_t head_ = 0, size_ = 0;   // bufor cykliczny
    std::size_t n_ = 0;                 // ile wartości (bez NaN) w statystykach
    std::size_t nan_count_ = 0;
    std::size_t since_resync_ = 0;
    double mean_ = 0.0, m2_ = 0.0;
    double err_ = 0.0;                  // oszacowany błąd m2 od ostatniego przeliczenia
};

// Wersje "na całej serii" - to, czego będzie używać Python. Pierwsze window-1 wyników = NaN.
inline std::vector<double> rolling_mean(const double* x, std::size_t n, std::size_t window) {
    RollingWindow w(window);
    std::vector<double> out(n);
    for (std::size_t i = 0; i < n; ++i) { w.push(x[i]); out[i] = w.mean(); }
    return out;
}

inline std::vector<double> rolling_std(const double* x, std::size_t n, std::size_t window, int ddof = 1) {
    RollingWindow w(window);
    std::vector<double> out(n);
    for (std::size_t i = 0; i < n; ++i) { w.push(x[i]); out[i] = w.stddev(ddof); }
    return out;
}

// Z-score bieżącej wartości względem okna, które ją zawiera.
inline std::vector<double> rolling_zscore(const double* x, std::size_t n, std::size_t window, int ddof = 1) {
    RollingWindow w(window);
    std::vector<double> out(n);
    for (std::size_t i = 0; i < n; ++i) { w.push(x[i]); out[i] = w.zscore(x[i], ddof); }
    return out;
}

}  // namespace quantpulse
