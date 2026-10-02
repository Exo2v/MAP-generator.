package io.github.exo2v.vantraya.core;

/**
 * Fixed percentile bounds that stand in for the offline engine's whole-raster statistics.
 *
 * <p>The offline engine normalises several noise fields against the 1st/99th (or 2nd/98th, 3rd/97th ...)
 * percentile of the <em>entire</em> 8,000 x 8,000 raster ({@code windows()}, {@code stretch01()}).
 * A chunk generator never sees the whole raster, so each of those percentiles is replaced by a
 * constant: the median of the per-seed percentile over many world seeds, measured on the same
 * canvas by {@code tools/Calibrate}. The fields keep their structure; only the stretch bounds are fixed.
 *
 * <p>{@link #SPEC_SEED_REFERENCE} holds the exact bounds the offline engine used for the shipped world
 * (seed 20250929); the cross-check tests use it to compare the two implementations like for like.
 */
public record Calibration(
        /** p1/p99 of the continentalness field before its landmark windows are applied. */
        double contLo, double contHi,
        /** p1/p99 of the raw erosion fBm (4 octaves, scale 1500). */
        double eroLo, double eroHi,
        /** p1/p99 of the raw ridges fBm (5 octaves, scale 1250). */
        double ridLo, double ridHi,
        /** p3/p97 of the Veil-floor fBm (3 octaves, scale 1400). */
        double veilLo, double veilHi,
        /** p3/p97 of the dune envelope fBm (3 octaves, scale 1500). */
        double dunesEnvLo, double dunesEnvHi,
        /** p4/p96 of the dune mesa fBm (3 octaves, scale 760). */
        double dunesMesaLo, double dunesMesaHi,
        /** p6/p94 of the barrier-sandbar fBm (2 octaves, scale 320). */
        double reachBarLo, double reachBarHi,
        /** p8/p92 of the coral-atoll fBm (3 octaves, scale 280). */
        double reachAtollLo, double reachAtollHi,
        /** p2/p98 of the regional temperature fBm (4 octaves, scale 3200). */
        double regionalTempLo, double regionalTempHi,
        /** min/max of the continentality fBm (3 octaves, scale 1920). */
        double continentalityLo, double continentalityHi,
        /** min/max of the humidity patch fBm (3 octaves, scale 1600). */
        double humPatchLo, double humPatchHi) {

    /** Exact bounds of the offline engine's shipped build (seed 20250929, cell 8 raster). */
    public static final Calibration SPEC_SEED_REFERENCE = new Calibration(
            -1.0337748796225936, 0.6907995617291451,
            -0.4342081763857051, 0.47011034623467113,
            -0.3932500951823752, 0.3914823333011887,
            -0.3927579240145674, 0.38761874167594546,
            -0.3343445695030304, 0.3706797414510703,
            -0.3444938559688709, 0.3607326617793877,
            -0.3567734671730091, 0.35415666815267544,
            -0.27926030065822294, 0.28127225155612945,
            -0.3334422197997543, 0.28164527321646365,
            -0.48731670342895966, 0.5700047664488169,
            -0.49770511665007977, 0.5695283662788804);

    /**
     * Seed-independent defaults: the median of the per-seed percentiles over 64 world seeds, each
     * measured on a 40-block sample of the canvas (see {@code tools/Calibrate}). The reference build's own
     * bounds all fall inside the seed-to-seed spread of these medians.
     */
    public static final Calibration DEFAULT = new Calibration(
            -0.985684, 0.700539,
            -0.419147, 0.411268,
            -0.399802, 0.396417,
            -0.370880, 0.378858,
            -0.376001, 0.373918,
            -0.344936, 0.345869,
            -0.356302, 0.355922,
            -0.282311, 0.282838,
            -0.353293, 0.341042,
            -0.560103, 0.563526,
            -0.578587, 0.568740);
}
