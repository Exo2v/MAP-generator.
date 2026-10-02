package io.github.exo2v.vantraya.core;

import java.util.Arrays;

/**
 * Catmull-Rom spline through control points, flat outside them - the "World Machine style"
 * spline primitive the specification's height, coast and climate curves are written in
 * (HANDOFF section 5.2). Port of {@code mg/core/noise.py: spline()}.
 */
public final class Spline {
    private final double[] xs;
    private final double[] ys;
    private final double[] m;

    public Spline(double[][] points) {
        double[][] pts = points.clone();
        Arrays.sort(pts, (a, b) -> Double.compare(a[0], b[0]));
        int n = pts.length;
        if (n < 2) {
            throw new IllegalArgumentException("a spline needs at least two control points");
        }
        xs = new double[n];
        ys = new double[n];
        for (int i = 0; i < n; i++) {
            xs[i] = pts[i][0];
            ys[i] = pts[i][1];
        }
        m = new double[n];
        for (int i = 1; i < n - 1; i++) {
            m[i] = (ys[i + 1] - ys[i - 1]) / (xs[i + 1] - xs[i - 1] + 1e-12);
        }
        double span0 = Math.max(xs[1] - xs[0], 1e-9);
        double spanN = Math.max(xs[n - 1] - xs[n - 2], 1e-9);
        m[0] = (ys[1] - ys[0]) / span0;
        m[n - 1] = (ys[n - 1] - ys[n - 2]) / spanN;
    }

    public double eval(double t) {
        int n = xs.length;
        if (t <= xs[0]) {
            return ys[0];
        }
        if (t >= xs[n - 1]) {
            return ys[n - 1];
        }
        // largest idx with xs[idx] <= t  (np.searchsorted(side="right") - 1), clipped to [0, n-2]
        int lo = 0;
        int hi = n - 1;
        while (hi - lo > 1) {
            int mid = (lo + hi) >>> 1;
            if (xs[mid] <= t) {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        int idx = Math.min(lo, n - 2);
        double x0 = xs[idx];
        double x1 = xs[idx + 1];
        double y0 = ys[idx];
        double y1 = ys[idx + 1];
        double h = Math.max(x1 - x0, 1e-9);
        double u = Mathx.saturate((t - x0) / h);
        double m0 = m[idx] * h;
        double m1 = m[idx + 1] * h;
        double u2 = u * u;
        double u3 = u2 * u;
        return (2 * u3 - 3 * u2 + 1) * y0
                + (u3 - 2 * u2 + u) * m0
                + (-2 * u3 + 3 * u2) * y1
                + (u3 - u2) * m1;
    }

    /** Piecewise-linear interpolation like {@code np.interp} (flat outside the table). */
    public static double interp(double x, double[] xp, double[] fp) {
        int n = xp.length;
        if (x < xp[0]) {
            return fp[0];
        }
        if (x >= xp[n - 1]) {
            return fp[n - 1];
        }
        int lo = 0;
        int hi = n - 1;
        while (hi - lo > 1) {
            int mid = (lo + hi) >>> 1;
            if (xp[mid] <= x) {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        double dx = xp[lo + 1] - xp[lo];
        if (dx == 0.0) {
            return fp[lo];
        }
        return fp[lo] + (fp[lo + 1] - fp[lo]) * (x - xp[lo]) / dx;
    }
}
