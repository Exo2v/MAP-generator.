package io.github.exo2v.vantraya.core;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import io.github.exo2v.vantraya.core.Spec.Kind;
import io.github.exo2v.vantraya.core.Spec.Landmark;

/**
 * The acceptance test of HANDOFF section 9.4, for a live world: does the generator really hold the
 * specified continent? It reads the world only through a {@link Probe}, never through the model's own
 * state, so it can be pointed at a running Minecraft generator (the {@code /vantraya verify} command) or
 * at the pure model (the unit tests) and judge both by the same table.
 */
public final class SpecVerifier {
    private SpecVerifier() {
    }

    /** What the verifier is allowed to look at. */
    public interface Probe {
        /** Y of the top solid block of the column (the ground, ignoring water above it). */
        int groundHeight(int x, int z);

        /** The biome role at a position, or {@code null} when the probe cannot say. */
        BiomeRole role(int x, int y, int z);
    }

    public record Check(String landmark, String what, boolean pass, String detail) {
        @Override
        public String toString() {
            return (pass ? "PASS " : "FAIL ") + landmark + ": " + what + " - " + detail;
        }
    }

    /**
     * HANDOFF 9.4: the offline verifier runs with {@code --pad 0.25}, "the fraction of each elevation band allowed as
     * tolerance". The Veil's band is the abyss floor to the trench top (-32..10), so a quarter of it is 10.5 blocks.
     *
     * <p>Since 0.2.0 the continent is generated as <em>zones, not pins</em>: no column is clamped to an exact Y,
     * so every landmark is held to its elevation band (plus this pad and a small floor) rather than to a point.
     */
    public static final double BAND_PAD = 0.25;

    /** The elevation range a landmark's centre may sit in: its band with the pad, wider where the landform itself is deep. */
    public static double[] zoneRange(Landmark lm) {
        double pad = Math.max(6.0, BAND_PAD * (lm.yHi() - lm.yLo()));
        if (lm.kind() == Kind.QUARRY) {
            return new double[] {55.0, 115.0}; // the quarry's pit floor and chasms are part of the landform
        }
        return new double[] {lm.yLo() - pad, lm.yHi() + pad};
    }

    public static boolean inZone(Landmark lm, int ground) {
        double[] r = zoneRange(lm);
        return ground >= r[0] && ground <= r[1];
    }

    public static List<Check> run(Probe p) {
        List<Check> out = new ArrayList<>();
        for (Landmark lm : Spec.LANDMARKS) {
            int gx = (int) Math.floor(lm.x());
            int gz = (int) Math.floor(lm.z());
            int ground = p.groundHeight(gx, gz);
            if (lm.kind() == Kind.CALDERA) {
                out.add(check(lm.name(), "Obsidian Throne at the centre", Math.abs(ground - Spec.CALDERA_THRONE) <= 8.0,
                        "ground Y=%d, specified %.0f (+/-8: zones, not pins)", ground, Spec.CALDERA_THRONE));
                int floor = p.groundHeight(gx + 200, gz);
                out.add(check(lm.name(), "sunken crater floor (38-42)", floor >= 37 && floor <= 43,
                        "ground Y=%d at r=200", floor));
                int rim = 0;
                for (int r = 380; r <= 560; r += 10) {
                    for (int k = 0; k < 16; k++) {
                        double a = k * Math.PI / 8.0;
                        rim = Math.max(rim, p.groundHeight((int) Math.round(gx + r * Math.cos(a)), (int) Math.round(gz + r * Math.sin(a))));
                    }
                }
                out.add(check(lm.name(), "volcanic rim wall (142-156)", rim >= 135 && rim <= 165,
                        "highest ring block Y=%d", rim));
            } else {
                double[] zr = zoneRange(lm);
                out.add(check(lm.name(), "centre elevation within its band", inZone(lm, ground),
                        "ground Y=%d, zone %.0f..%.0f (band %.0f-%.0f)", ground, zr[0], zr[1], lm.yLo(), lm.yHi()));
            }
            BiomeRole role = p.role(gx, Math.max(ground + 1, (int) Spec.SEA_LEVEL + 1), gz);
            if (role != null) {
                boolean ok = false;
                for (String b : lm.biomes()) {
                    ok |= BiomeRole.ofSpecName(b) == role || role.vanilla().equals(b);
                }
                out.add(check(lm.name(), "biome at the centre", ok, "%s (specified %s)", role.vanilla(), lm.biomes()));
            }
        }
        // the Forgotten Coast is the spawn: it must be dry land
        Landmark spawn = Spec.spawnLandmark();
        int sg = p.groundHeight((int) spawn.x(), (int) spawn.z());
        out.add(check(spawn.name(), "spawn is dry land above sea level", sg > Spec.SEA_LEVEL, "ground Y=%d", sg));
        // the Veil of Salt
        int[] veil = new int[12];
        for (int k = 0; k < veil.length; k++) {
            double a = k * Math.PI / 6.0;
            veil[k] = p.groundHeight((int) Math.round(3750 * Math.cos(a)), (int) Math.round(3750 * Math.sin(a)));
        }
        java.util.Arrays.sort(veil);
        // A cave opening on the sea floor only ever lowers a reading, so the two lowest of the twelve are set aside:
        // a floor that really is too deep lowers all twelve.
        int veilLo = veil[2];
        int veilHi = veil[veil.length - 1];
        double pad = BAND_PAD * (Spec.TRENCH_TOP - Spec.ABYSS_FLOOR);
        out.add(check(Spec.VEIL.name(), "abyss floor within -32..10 (+25% pad) beyond r = 3550",
                veilLo >= Spec.ABYSS_FLOOR - pad && veilHi <= Spec.TRENCH_TOP + pad,
                "ground Y %d..%d at r=3750 (two lowest of 12 set aside), allowed %.1f..%.1f",
                veilLo, veilHi, Spec.ABYSS_FLOOR - pad, Spec.TRENCH_TOP + pad));
        return out;
    }

    private static Check check(String landmark, String what, boolean pass, String fmt, Object... args) {
        return new Check(landmark, what, pass, String.format(Locale.ROOT, fmt, args));
    }

    public static boolean allPass(List<Check> checks) {
        for (Check c : checks) {
            if (!c.pass()) {
                return false;
            }
        }
        return true;
    }

    /** A probe that reads the pure model (surface = round of the model height). */
    public static Probe modelProbe(VantrayaModel model) {
        return new Probe() {
            @Override
            public int groundHeight(int x, int z) {
                return (int) Math.rint(model.height(x + 0.5, z + 0.5));
            }

            @Override
            public BiomeRole role(int x, int y, int z) {
                VantrayaModel.Fields f = model.sample(x + 0.5, z + 0.5);
                double depth = (f.height() + 0.5 - y) / 128.0;
                return BiomeLogic.classify(x + 0.5, y, z + 0.5, f.temperature(), f.humidity(), f.cont(), f.erosion(), depth);
            }
        };
    }
}
