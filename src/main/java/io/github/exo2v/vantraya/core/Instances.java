package io.github.exo2v.vantraya.core;

import java.util.ArrayList;
import java.util.List;
import java.util.SplittableRandom;

import io.github.exo2v.vantraya.core.Spec.Landmark;

/**
 * Spline-guided mountain instances (the offline engine's {@code mg/core/instances.py}).
 *
 * <p>Pure gradient noise only makes amorphous mounds; real cordilleras are structural. The Glacial
 * Spine is built the way the blueprint describes: a Catmull-Rom fault spline, Poisson-disk stations
 * along it, and a primitive stamped at each - an <em>arete</em> ridge
 * {@code H * exp(-|v|/sigma) * (1 - (u/L)^2)} or a pyramidal <em>peak</em>
 * {@code H * (1 - r/R)^gamma * (1 + alpha * cos(k * theta))} - composited with a polynomial
 * smooth-max so crests stay sharp and cols stay smooth.
 *
 * <p>The instance list is computed once per world seed; evaluating the relief at a point only visits
 * the instances whose footprint covers it, so the field is local and thread-safe.
 */
public final class Instances {
    private Instances() {
    }

    public enum Type {
        PEAK, ARETE
    }

    /** One stamped primitive. {@code seed} drives its edge noise. */
    public record Instance(Type type, double cx, double cz, double height, double angle,
                           double length, double sigma, double radius, int arms, double gamma,
                           double armAmp, double blendK, long seed) {
        /** Half-extent of the axis-aligned window the primitive is stamped into. */
        public double reach() {
            return type == Type.ARETE ? Math.max(length * 0.5, sigma * 4.0) : radius;
        }
    }

    // ---------------------------------------------------------------------------------------
    // spline maths
    // ---------------------------------------------------------------------------------------

    /** Uniform samples of a Catmull-Rom spline through the waypoints: columns x, z, dx, dz. */
    public static double[][] catmullRom(double[][] waypoints, int samples) {
        int n = waypoints.length - 1;
        double[][] p = new double[waypoints.length + 2][2];
        p[0][0] = waypoints[0][0] + (waypoints[0][0] - waypoints[1][0]);
        p[0][1] = waypoints[0][1] + (waypoints[0][1] - waypoints[1][1]);
        for (int i = 0; i < waypoints.length; i++) {
            p[i + 1] = waypoints[i];
        }
        int last = waypoints.length - 1;
        p[waypoints.length + 1][0] = waypoints[last][0] + (waypoints[last][0] - waypoints[last - 1][0]);
        p[waypoints.length + 1][1] = waypoints[last][1] + (waypoints[last][1] - waypoints[last - 1][1]);

        double[][] out = new double[samples][4];
        for (int s = 0; s < samples; s++) {
            double t = samples == 1 ? 0.0 : (double) n * s / (samples - 1);
            int seg = Mathx.clamp((int) Math.floor(t), 0, n - 1);
            double u = t - seg;
            double[] p0 = p[seg];
            double[] p1 = p[seg + 1];
            double[] p2 = p[seg + 2];
            double[] p3 = p[seg + 3];
            double u2 = u * u;
            double u3 = u2 * u;
            double h00 = 2 * u3 - 3 * u2 + 1;
            double h10 = u3 - 2 * u2 + u;
            double h01 = -2 * u3 + 3 * u2;
            double h11 = u3 - u2;
            double d00 = 6 * u2 - 6 * u;
            double d10 = 3 * u2 - 4 * u + 1;
            double d01 = -6 * u2 + 6 * u;
            double d11 = 3 * u2 - 2 * u;
            for (int a = 0; a < 2; a++) {
                double m1 = (p2[a] - p0[a]) * 0.5;
                double m2 = (p3[a] - p1[a]) * 0.5;
                out[s][a] = h00 * p1[a] + h10 * m1 + h01 * p2[a] + h11 * m2;
                out[s][2 + a] = d00 * p1[a] + d10 * m1 + d01 * p2[a] + d11 * m2;
            }
        }
        return out;
    }

    /** Poisson-disk stations along a sampled spline: {@code {x, z, angle}} per station. */
    public static List<double[]> poissonStations(double[][] spline, int count, double minSpacing,
                                                 double jitter, SplittableRandom rng) {
        int n = spline.length;
        List<double[]> out = new ArrayList<>();
        if (n == 0 || count <= 0) {
            return out;
        }
        int[] order = new int[n];
        for (int i = 0; i < n; i++) {
            order[i] = i;
        }
        for (int i = n - 1; i > 0; i--) { // Fisher-Yates
            int j = rng.nextInt(i + 1);
            int tmp = order[i];
            order[i] = order[j];
            order[j] = tmp;
        }
        List<Integer> chosen = new ArrayList<>();
        double spacing = minSpacing;
        int tries = 0;
        while (chosen.size() < count && tries < 40) {
            for (int idx : order) {
                if (chosen.size() >= count) {
                    break;
                }
                boolean ok = true;
                for (int j : chosen) {
                    double dx = spline[idx][0] - spline[j][0];
                    double dz = spline[idx][1] - spline[j][1];
                    if (dx * dx + dz * dz < spacing * spacing) {
                        ok = false;
                        break;
                    }
                }
                if (ok) {
                    chosen.add(idx);
                }
            }
            if (chosen.size() < count) {
                spacing *= 0.7;
                tries++;
            }
        }
        for (int idx : chosen) {
            double jx = (rng.nextDouble() * 2.0 - 1.0) * jitter;
            double jz = (rng.nextDouble() * 2.0 - 1.0) * jitter;
            double angle = Math.atan2(spline[idx][3], spline[idx][2]);
            out.add(new double[] {spline[idx][0] + jx, spline[idx][1] + jz, angle});
        }
        return out;
    }

    // ---------------------------------------------------------------------------------------
    // the two landmark instance sets
    // ---------------------------------------------------------------------------------------

    private static double uniform(SplittableRandom rng, double lo, double hi) {
        return lo + (hi - lo) * rng.nextDouble();
    }

    private static int integers(SplittableRandom rng, int lo, int hiExclusive) {
        return lo + rng.nextInt(hiExclusive - lo);
    }

    /**
     * The Solitary Glacial Spine: a bowed fault spline along the long axis of the box, 30
     * Poisson-spaced stations (min spacing 200, jitter 60) plus a summit pinned at the landmark's
     * exact centre; 45 % peaks (radius 150-210, 3-5 arms) and 55 % arete ridges (length 320-620,
     * sigma 38-62).
     */
    public static List<Instance> buildCordillera(Landmark lm, long shaperSeed) {
        double span = lm.x2() - lm.x1();
        double[][] waypoints = {
                {lm.x1() + 0.02 * span, lm.z() - 240.0},
                {lm.x1() + 0.26 * span, lm.z() + 140.0},
                {lm.x1() + 0.50 * span, lm.z() - 60.0},
                {lm.x1() + 0.74 * span, lm.z() - 280.0},
                {lm.x1() + 0.98 * span, lm.z() - 20.0}};
        double[][] spline = catmullRom(waypoints, 768);
        SplittableRandom rng = new SplittableRandom(shaperSeed & 0xFFFFFFFFL);
        List<double[]> stations = poissonStations(spline, 30, 200.0, 60.0, rng);
        stations.add(0, new double[] {lm.x(), lm.z(), 0.0}); // the specification pins a summit at the centre
        List<Instance> out = new ArrayList<>();
        for (int i = 0; i < stations.size(); i++) {
            double[] s = stations.get(i);
            if (rng.nextDouble() < 0.45) {
                double radius = uniform(rng, 150.0, 210.0);
                int arms = integers(rng, 3, 6);
                out.add(new Instance(Type.PEAK, s[0], s[1], Spec.SPINE_HIGH - 10.0, 0.0, 0.0, 0.0,
                        radius, arms, 1.9, 0.26, 26.0, shaperSeed + i));
            } else {
                double length = uniform(rng, 320.0, 620.0);
                double sigma = uniform(rng, 38.0, 62.0);
                out.add(new Instance(Type.ARETE, s[0], s[1], Spec.SPINE_HIGH - 24.0, s[2], length,
                        sigma, 0.0, 4, 1.8, 0.0, 26.0, shaperSeed + i));
            }
        }
        return out;
    }

    /** The Hermit's Spire: 6-9 solitary granite needles scattered within 420 blocks of the centre. */
    public static List<Instance> buildNeedles(Landmark lm, long shaperSeed) {
        SplittableRandom rng = new SplittableRandom((shaperSeed + 901) & 0xFFFFFFFFL);
        int count = integers(rng, 6, 10);
        List<Instance> out = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            double x = lm.x() + uniform(rng, -420.0, 420.0);
            double z = lm.z() + uniform(rng, -420.0, 420.0);
            double height = uniform(rng, 38.0, 46.0);
            double radius = uniform(rng, 46.0, 84.0);
            int arms = integers(rng, 3, 5);
            out.add(new Instance(Type.PEAK, x, z, height, 0.0, 0.0, 0.0, radius, arms, 2.4, 0.18,
                    18.0, shaperSeed + i));
        }
        return out;
    }

    // ---------------------------------------------------------------------------------------
    // evaluation
    // ---------------------------------------------------------------------------------------

    /**
     * Relief of the whole instance set at {@code (x, z)}, composited in station order with the
     * support-tapered smooth-max the offline engine uses. {@code noiseOffset} converts world
     * coordinates to the offline raster's grid-local frame, which fixes the phase of the edge noise.
     */
    public static double relief(List<Instance> list, double x, double z, double noiseOffset) {
        double relief = 0.0;
        for (Instance in : list) {
            double reach = in.reach();
            if (Math.abs(x - in.cx) > reach || Math.abs(z - in.cz) > reach) {
                continue;
            }
            double dx = x - in.cx;
            double dz = z - in.cz;
            double nx = x + noiseOffset;
            double nz = z + noiseOffset;
            double support;
            double inst;
            if (in.type == Type.ARETE) {
                double cos = Math.cos(in.angle);
                double sin = Math.sin(in.angle);
                double u = dx * cos + dz * sin;
                double v = -dx * sin + dz * cos;
                double along = Mathx.saturate(1.0 - sq(u / Math.max(in.length * 0.5, 1e-6)));
                double cross = Math.exp(-Math.abs(v) / Math.max(in.sigma, 1e-6));
                support = along * cross;
                inst = in.height * support;
                inst *= 1.0 + 0.18 * (Noise.fbm(nx, nz, 3, 40.0, in.seed + 17) * 2.0 - 1.0);
            } else {
                double r = Math.hypot(dx, dz);
                double theta = Math.atan2(dz, dx);
                double radial = Math.pow(Mathx.saturate(1.0 - r / Math.max(in.radius, 1e-6)), in.gamma);
                support = radial;
                inst = in.height * radial * (1.0 + in.armAmp * Math.cos(Math.max(1, in.arms) * theta));
                inst *= 1.0 + 0.12 * (Noise.fbm(nx, nz, 2, 55.0, in.seed + 29) * 2.0 - 1.0);
            }
            double sm = Mathx.smoothMax(relief, inst, in.blendK);
            double t = Mathx.saturate(support * 6.0);
            t = t * t * (3.0 - 2.0 * t);
            relief = relief + (sm - relief) * t;
        }
        return relief;
    }

    private static double sq(double v) {
        return v * v;
    }
}
