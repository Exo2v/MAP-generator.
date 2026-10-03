package io.github.exo2v.vantraya.core;

import java.util.Arrays;
import java.util.PriorityQueue;

/**
 * The drainage network of the continent: where water flows, how wide it is, and the height of its surface.
 *
 * <p>The play test of 0.1.2 showed what a river field built from noise looks like: channels that wander over
 * ridges, stop in mid-slope, and hold water at a different level in every column. Real rivers are none of
 * those things, because they are not a shape — they are a <em>bookkeeping</em> of where water would go. This
 * class does that bookkeeping once per world seed on a lattice, and every column of the world then reads the
 * result:
 *
 * <ol>
 *   <li><b>Depressionless terrain.</b> A priority flood fills every closed basin to its spill level. The
 *       filled height is monotonically decreasing along every flow path, and every filled cell above its
 *       natural ground is a lake with a flat surface at its spill height — real basins, not noise blobs.</li>
 *   <li><b>D8 flow and accumulation.</b> Each cell drains to its lowest neighbour and accumulates the cells
 *       upstream of it. Accumulation <em>is</em> the river: a channel is where enough catchment drains
 *       through a cell, its width follows the catchment, and because flow follows the terrain downhill a
 *       channel can never cross a ridge or start on a peak.</li>
 *   <li><b>A monotone water surface.</b> Walking the network upstream from every mouth, the surface never
 *       drops going downstream and never climbs more than {@link #MAX_RISE} per cell going upstream, so the
 *       water runs downhill as one continuous surface per reach — with any drop a declared one, of at most
 *       {@link #MAX_RISE} blocks, instead of a per-column staircase.</li>
 *   <li><b>A distance field</b> to the nearest water cell, from which every column derives its cross-section:
 *       bed under the surface inside the channel, smooth banks outside.</li>
 * </ol>
 *
 * <p>Masks: cells the specification keeps dry (the caldera, the closed basins of the dunes and the quarry)
 * act as <em>sinks</em> — water drains into them and away, no lake fills behind them, and no channel is cut
 * through them. Landmark pin zones suppress the carve in {@link VantrayaModel}, as before.
 */
public final class Drainage {

    /** One lattice cell's terrain, as seen before any water is cut into it. */
    public record CellSample(double height, boolean noWater, boolean noLake) {
    }

    @FunctionalInterface
    public interface Terrain {
        CellSample sample(double x, double z);
    }

    /** What a column reads out of the network. */
    public record At(double waterY, double halfWidth, double dist, double channel, double lake) {

        /** True where the network has water to place (inside a channel or a lake). */
        public boolean wet() {
            return channel > 0.015 || lake > 0.015;
        }
    }

    /** A column outside the network: no channel, no lake. */
    public static final At DRY = new At(VantrayaModel.NO_WATER_LINE, 0.0, 1.0E9, 0.0, 0.0);

    /** Lattice cell size, blocks. */
    public static final int CELL = 16;

    /** The lattice covers the canvas plus margin: x, z in [-HALF, HALF]. */
    public static final int HALF = 4224;

    /**
     * Catchment (in cells) at which a channel begins: a headwater spring. One cell is 16 × 16 blocks, so a
     * head drains about 260 × 256 m² ≈ 0.07 km² and the widest trunks carry hundreds of times that.
     */
    public static final int HEAD_CATCHMENT = 260;

    /** No channel is cut above this ground height: the peaks stay dry. */
    public static final double CHANNEL_MAX_H = 225.0;

    /** The water surface may climb at most this many blocks per cell going upstream (a 5.6 % slope). */
    public static final double MAX_RISE = 0.9;

    /** The channel bed sits this far below the water surface where the natural ground is high. */
    private static final double BED_DROP = 1.2;

    /** Lake beds sit this far below their spill level. */
    public static final double LAKE_DEPTH = 3.5;

    private final int n; // cells per axis
    private final float[] natural;
    private final float[] filled;
    private final float[] waterY;
    private final float[] sqDist;
    private final float[] halfWidth;
    private final float[] lakeK;
    private final int[] flowTo;
    private final int[] accum;
    private final boolean[] channel;
    private final boolean[] lake;
    private final boolean[] noWater;
    private final boolean[] noLake;

    public Drainage(Terrain terrain) {
        this.n = HALF * 2 / CELL + 1;
        int cells = n * n;
        natural = new float[cells];
        filled = new float[cells];
        waterY = new float[cells];
        sqDist = new float[cells];
        halfWidth = new float[cells];
        lakeK = new float[cells];
        flowTo = new int[cells];
        accum = new int[cells];
        channel = new boolean[cells];
        lake = new boolean[cells];
        noWater = new boolean[cells];
        noLake = new boolean[cells];
        build(terrain);
    }

    // ---------------------------------------------------------------------------------------
    // build
    // ---------------------------------------------------------------------------------------

    private double cx(int i) {
        return -HALF + (long) i * CELL;
    }

    private void build(Terrain terrain) {
        // ---- 1. the terrain, seen from above ------------------------------------------------
        for (int j = 0; j < n; j++) {
            for (int i = 0; i < n; i++) {
                int idx = j * n + i;
                CellSample s = terrain.sample(cx(i), cx(j));
                natural[idx] = (float) s.height();
                noWater[idx] = s.noWater();
                noLake[idx] = s.noLake();
            }
        }

        // ---- 2. priority flood: depressionless terrain, lakes at their spill levels -----------
        boolean[] outlet = new boolean[n * n];
        for (int j = 0; j < n; j++) {
            for (int i = 0; i < n; i++) {
                int idx = j * n + i;
                boolean edge = i == 0 || j == 0 || i == n - 1 || j == n - 1;
                outlet[idx] = edge || natural[idx] <= Spec.SEA_LEVEL + 0.35f || noWater[idx] || noLake[idx];
            }
        }
        System.arraycopy(natural, 0, filled, 0, natural.length);
        boolean[] seen = new boolean[n * n];
        PriorityQueue<int[]> pq = new PriorityQueue<>((a, b) -> {
            int c = Float.compare(filled[a[0]], filled[b[0]]);
            return c != 0 ? c : Integer.compare(a[0], b[0]);
        });
        for (int idx = 0; idx < n * n; idx++) {
            if (outlet[idx]) {
                seen[idx] = true;
                pq.add(new int[] {idx});
            }
        }
        int[] di = {1, -1, 0, 0};
        int[] dj = {0, 0, 1, -1};
        while (!pq.isEmpty()) {
            int idx = pq.poll()[0];
            int i = idx % n;
            int j = idx / n;
            for (int k = 0; k < 4; k++) {
                int i2 = i + di[k];
                int j2 = j + dj[k];
                if (i2 < 0 || j2 < 0 || i2 >= n || j2 >= n) {
                    continue;
                }
                int q = j2 * n + i2;
                if (seen[q]) {
                    continue;
                }
                seen[q] = true;
                filled[q] = Math.max(natural[q], filled[idx] + 1.0E-3f);
                pq.add(new int[] {q});
            }
        }

        // ---- 3. D8 flow: every cell drains to its lowest neighbour --------------------------
        for (int j = 0; j < n; j++) {
            for (int i = 0; i < n; i++) {
                int idx = j * n + i;
                flowTo[idx] = -1;
                if (natural[idx] <= Spec.SEA_LEVEL) {
                    continue; // the sea: nowhere to drain
                }
                int best = -1;
                float bestH = filled[idx];
                for (int dj2 = -1; dj2 <= 1; dj2++) {
                    for (int di2 = -1; di2 <= 1; di2++) {
                        if (di2 == 0 && dj2 == 0) {
                            continue;
                        }
                        int i2 = i + di2;
                        int j2 = j + dj2;
                        if (i2 < 0 || j2 < 0 || i2 >= n || j2 >= n) {
                            continue;
                        }
                        int q = j2 * n + i2;
                        if (filled[q] < bestH || (filled[q] == bestH && best < 0)) {
                            bestH = filled[q];
                            best = q;
                        }
                    }
                }
                flowTo[idx] = best;
            }
        }

        // ---- 4. flow accumulation: cells upstream of a cell, counted ------------------------
        Integer[] order = new Integer[n * n];
        for (int idx = 0; idx < n * n; idx++) {
            order[idx] = idx;
        }
        Arrays.sort(order, (a, b) -> Float.compare(filled[b], filled[a])); // upstream first
        Arrays.fill(accum, 1);
        for (int idx : order) {
            int to = flowTo[idx];
            if (to >= 0) {
                accum[to] += accum[idx];
            }
        }

        // ---- 5. channels and lakes ----------------------------------------------------------
        for (int idx = 0; idx < n * n; idx++) {
            lake[idx] = filled[idx] > natural[idx] + 0.10f && !noWater[idx] && !noLake[idx];
            channel[idx] = !lake[idx] && !noWater[idx] && natural[idx] > Spec.SEA_LEVEL - 1.0f
                    && natural[idx] < CHANNEL_MAX_H && accum[idx] >= HEAD_CATCHMENT;
            if (channel[idx]) {
                // never narrower than seven blocks: the engine samples the height field on a 4-block
                // grid and a slimmer notch comes out of that smoothing as a dry seam
                halfWidth[idx] = (float) Mathx.clamp(3.5 + 1.35 * Math.log(accum[idx] / (double) HEAD_CATCHMENT), 3.5, 12.0);
            }
            if (lake[idx]) {
                lakeK[idx] = (float) Mathx.smoothstep(0.15, 1.2, filled[idx] - natural[idx]);
            }
        }

        // ---- 6. the water surface, walking the network upstream from every mouth -------------
        // upstream adjacency as a linked list: head[], next[]
        int[] head = new int[n * n];
        int[] next = new int[n * n];
        Arrays.fill(head, -1);
        for (int idx = 0; idx < n * n; idx++) {
            int to = flowTo[idx];
            if (to >= 0) {
                next[idx] = head[to];
                head[to] = idx;
            }
        }
        Arrays.fill(waterY, Float.NaN);
        int[] queue = new int[n * n];
        int qh = 0;
        int qt = 0;
        for (int idx = 0; idx < n * n; idx++) {
            if (lake[idx]) {
                waterY[idx] = filled[idx]; // flat at the spill level
                queue[qt++] = idx;
            } else if (flowTo[idx] < 0) {
                waterY[idx] = natural[idx] <= Spec.SEA_LEVEL + 0.5f
                        ? (float) Math.min(natural[idx] - 0.6, Spec.SEA_LEVEL + 0.3)
                        : (float) (natural[idx] - BED_DROP); // a sink in a dry basin: a playa floor
                queue[qt++] = idx;
            }
        }
        while (qh < qt) {
            int d = queue[qh++];
            for (int u = head[d]; u >= 0; u = next[u]) {
                if (!Float.isNaN(waterY[u])) {
                    continue; // already given its surface (a lake queued at the start, say)
                }
                double y = lake[u] ? filled[u]
                        : Math.min(natural[u] - BED_DROP, waterY[d] + MAX_RISE);
                if (!lake[u]) {
                    // never dip meaningfully below the water it flows into (the shore of a lake it feeds)
                    y = Math.max(y, waterY[d] - 0.5);
                }
                waterY[u] = (float) y;
                queue[qt++] = u;
            }
        }
        for (int idx = 0; idx < n * n; idx++) {
            if (Float.isNaN(waterY[idx])) {
                waterY[idx] = (float) (natural[idx] - BED_DROP); // unreachable flat: harmless default
            }
        }

        // ---- 7. distance to the nearest water cell -------------------------------------------
        float[] f = new float[n * n];
        for (int idx = 0; idx < n * n; idx++) {
            f[idx] = channel[idx] || lake[idx] ? 0.0f : Float.MAX_VALUE / 4;
        }
        edt2d(f, sqDist, n);
    }

    /** Exact squared Euclidean distance transform (Felzenszwalb & Huttenlocher), in place per row/column. */
    private static void edt2d(float[] f, float[] d, int n) {
        float[] row = new float[n];
        float[] out = new float[n];
        for (int j = 0; j < n; j++) {
            System.arraycopy(f, j * n, row, 0, n);
            edt1d(row, out, n);
            System.arraycopy(out, 0, d, j * n, n);
        }
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                row[j] = d[j * n + i];
            }
            edt1d(row, out, n);
            for (int j = 0; j < n; j++) {
                d[j * n + i] = out[j];
            }
        }
    }

    private static void edt1d(float[] f, float[] d, int n) {
        int[] v = new int[n];
        float[] z = new float[n + 1];
        int k = 0;
        v[0] = 0;
        z[0] = Float.NEGATIVE_INFINITY;
        z[1] = Float.POSITIVE_INFINITY;
        for (int q = 1; q < n; q++) {
            float s;
            while (true) {
                s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0f * q - 2.0f * v[k]);
                if (s <= z[k]) {
                    k--;
                } else {
                    break;
                }
            }
            k++;
            v[k] = q;
            z[k] = s;
            z[k + 1] = Float.POSITIVE_INFINITY;
        }
        k = 0;
        for (int q = 0; q < n; q++) {
            while (z[k + 1] < q) {
                k++;
            }
            float dx = q - v[k];
            d[q] = dx * dx + f[v[k]];
        }
    }

    // ---------------------------------------------------------------------------------------
    // query
    // ---------------------------------------------------------------------------------------

    /** The network at a world position: water surface, channel width, distance to the channel, strengths. */
    public At at(double x, double z) {
        double gx = (x + HALF) / (double) CELL;
        double gz = (z + HALF) / (double) CELL;
        int i = (int) Math.floor(gx);
        int j = (int) Math.floor(gz);
        if (i < 0 || j < 0 || i >= n - 1 || j >= n - 1) {
            return DRY;
        }
        double fx = gx - i;
        double fz = gz - j;
        int i00 = j * n + i;
        int i10 = i00 + 1;
        int i01 = i00 + n;
        int i11 = i01 + 1;
        double w00 = (1 - fx) * (1 - fz);
        double w10 = fx * (1 - fz);
        double w01 = (1 - fx) * fz;
        double w11 = fx * fz;

        double c = w00 * chan(i00) + w10 * chan(i10) + w01 * chan(i01) + w11 * chan(i11);
        double l = w00 * lakeK[i00] + w10 * lakeK[i10] + w01 * lakeK[i01] + w11 * lakeK[i11];
        double dist = Math.sqrt(Math.max(0.0,
                w00 * sqDist[i00] + w10 * sqDist[i10] + w01 * sqDist[i01] + w11 * sqDist[i11]));

        // the surface and the width are read only from wet cells, so two channels never average together
        double ww = 0.0;
        double ys = 0.0;
        double ws = 0.0;
        double[] w = {w00, w10, w01, w11};
        int[] ids = {i00, i10, i01, i11};
        for (int q = 0; q < 4; q++) {
            int id = ids[q];
            double wet = Math.max(chan(id), lakeK[id]);
            double m = w[q] * wet;
            if (m > 0.0) {
                ww += m;
                ys += m * waterY[id];
                ws += m * (channel[id] ? halfWidth[id] : 6.0);
            }
        }
        if (ww < 0.02) {
            // no wet cell reaches here: strength from the ramp alone must not carve with a null surface
            return new At(VantrayaModel.NO_WATER_LINE, 0.0, dist, 0.0, 0.0);
        }
        return new At(ys / ww, ws / ww, dist, c, l);
    }

    private double chan(int idx) {
        return channel[idx] ? 1.0 : 0.0;
    }

    // ---------------------------------------------------------------------------------------
    // read-outs for tests and tools
    // ---------------------------------------------------------------------------------------

    public int cells() {
        return n * n;
    }

    public int cellIndex(double x, double z) {
        int i = (int) Math.round((x + HALF) / (double) CELL);
        int j = (int) Math.round((z + HALF) / (double) CELL);
        if (i < 0 || j < 0 || i >= n || j >= n) {
            return -1;
        }
        return j * n + i;
    }

    public double cellX(int idx) {
        return cx(idx % n);
    }

    public double cellZ(int idx) {
        return cx(idx / n);
    }

    public double naturalH(int idx) {
        return natural[idx];
    }

    public double filledH(int idx) {
        return filled[idx];
    }

    public double waterSurface(int idx) {
        return waterY[idx];
    }

    public int catchment(int idx) {
        return accum[idx];
    }

    public boolean isChannel(int idx) {
        return channel[idx];
    }

    public boolean isLake(int idx) {
        return lake[idx];
    }

    public int drainsTo(int idx) {
        return flowTo[idx];
    }
}
