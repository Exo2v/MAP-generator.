package io.github.exo2v.vantraya.core;

/**
 * The curves the continent is built from (HANDOFF section 5.2 and 5.6): the bearing-dependent coast
 * radius, the coast spline that turns distance-from-shoreline into continentalness, and the base
 * height spline that turns continentalness into blocks.
 */
public final class Tables {
    private Tables() {
    }

    /**
     * {@code R_coast} control points: compass bearing (degrees, 0 = +X east, 90 = +Z south) against the
     * distance from the origin to the shoreline in blocks. The coastline is deliberately not a circle:
     * the continent reaches furthest north (which puts the Glacial Spine inland) and is closest in the
     * south-west (which creates the drowned Sunken Reach).
     */
    private static final double[][] COAST_RADIUS = {
            {-180.0, 3350.0},
            {-135.0, 3320.0},
            {-90.0, 3480.0},
            {-45.0, 3260.0},
            {0.0, 3150.0},
            {30.0, 3120.0},
            {45.0, 3150.0},
            {70.0, 3080.0},
            {90.0, 3200.0},
            {120.0, 2860.0},
            {146.0, 2700.0},
            {180.0, 3150.0}};

    /** {@code r - R_coast} (blocks) -> continentalness, so the waterline lands on {@code Delta = 0}. */
    public static final Spline COAST_SPLINE = new Spline(new double[][] {
            {-2600.0, 0.60}, {-1200.0, 0.52}, {-700.0, 0.44}, {-400.0, 0.31}, {-150.0, 0.15},
            {-40.0, 0.05}, {0.0, -0.02}, {140.0, -0.20}, {450.0, -0.45}, {950.0, -0.70},
            {2600.0, -1.05}});

    /** Continentalness -> base elevation in blocks (spec section 1 feature table + land/ocean clamp rules). */
    public static final Spline BASE_SPLINE = new Spline(new double[][] {
            {-1.05, 20.0}, {-0.85, 30.0}, {-0.60, 40.0}, {-0.35, 48.0}, {-0.20, 53.0},
            {-0.02, 60.0}, {0.05, 63.0}, {0.12, 66.0}, {0.25, 68.0}, {0.45, 74.0},
            {0.65, 84.0}, {0.85, 98.0}, {1.15, 122.0}});

    private static final double[] WRAP_T_LEGACY;
    private static final double[] WRAP_R_LEGACY;
    private static final double[] WRAP_T_SEAMLESS;
    private static final double[] WRAP_R_SEAMLESS;

    static {
        // The table is wrapped +-360 degrees so the interpolation is periodic (np.interp on the tripled table).
        int n = COAST_RADIUS.length;
        WRAP_T_LEGACY = new double[3 * n];
        WRAP_R_LEGACY = new double[3 * n];
        for (int k = 0; k < 3; k++) {
            for (int i = 0; i < n; i++) {
                WRAP_T_LEGACY[k * n + i] = COAST_RADIUS[i][0] + (k - 1) * 360.0;
                WRAP_R_LEGACY[k * n + i] = COAST_RADIUS[i][1];
            }
        }
        // Seam fix: the table lists -180 deg (3350) and +180 deg (3150), which are the same bearing, so the
        // wrapped curve jumps 200 blocks along the west axis. Both ends become their mean.
        double[][] seamless = new double[n][];
        for (int i = 0; i < n; i++) {
            seamless[i] = COAST_RADIUS[i].clone();
        }
        double mean = 0.5 * (COAST_RADIUS[0][1] + COAST_RADIUS[n - 1][1]);
        seamless[0][1] = mean;
        seamless[n - 1][1] = mean;
        WRAP_T_SEAMLESS = new double[3 * n];
        WRAP_R_SEAMLESS = new double[3 * n];
        for (int k = 0; k < 3; k++) {
            for (int i = 0; i < n; i++) {
                WRAP_T_SEAMLESS[k * n + i] = seamless[i][0] + (k - 1) * 360.0;
                WRAP_R_SEAMLESS[k * n + i] = seamless[i][1];
            }
        }
    }

    /**
     * Distance from the origin to the shoreline along the bearing of {@code (x, z)}.
     *
     * @param seamless close the 200-block jump the offline table has across the west axis
     */
    public static double coastRadius(double x, double z, boolean seamless) {
        double theta = Math.toDegrees(Math.atan2(z, x));
        return seamless
                ? Spline.interp(theta, WRAP_T_SEAMLESS, WRAP_R_SEAMLESS)
                : Spline.interp(theta, WRAP_T_LEGACY, WRAP_R_LEGACY);
    }
}
