package io.github.exo2v.vantraya.router;

import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;

/**
 * A tiny evaluator for the density-function JSON of the noise router, enough to run Vantraya's
 * {@code final_density} without Minecraft: the arithmetic and shaping types, Y gradients, range choice, and the
 * cache/interpolation wrappers (which do not change values at lattice points). Noise terms evaluate to
 * zero - the mean of a noise - so the cave functions contribute their noise-free shape, and the
 * {@code vantraya_builder:field} type reads the real specification model.
 *
 * <p>Vanilla definitions referenced by id (caves, base 3D noise) come from {@code src/test/resources/vanilla-1.21.1}.
 */
final class DensityInterpreter {
    private final Map<String, JsonElement> definitions = new HashMap<>();
    private final Map<String, Double> overrides = new HashMap<>();
    private final VantrayaModel model;

    DensityInterpreter(VantrayaModel model) {
        this.model = model;
        // The vanilla cave functions are gated by noise: with every noise at exactly its mean (zero) they sit on their
        // toggle thresholds and would carve the whole world. Neutralise them so the test sees the terrain composition.
        overrides.put("minecraft:overworld/caves/noodle", 64.0);
        overrides.put("minecraft:overworld/caves/entrances", 1.0);
        overrides.put("minecraft:overworld/caves/spaghetti_2d", 1.0);
        overrides.put("minecraft:overworld/caves/spaghetti_roughness_function", 0.0);
        overrides.put("minecraft:overworld/caves/pillars", -1.0);
    }

    /** Force a referenced density function to a constant (tests use it to ask for the strongest cave carving). */
    void override(String id, double value) {
        overrides.put(id, value);
    }

    static JsonElement parseResource(String path) {
        try (InputStream in = DensityInterpreter.class.getResourceAsStream(path)) {
            if (in == null) {
                throw new IllegalStateException("missing resource " + path);
            }
            return JsonParser.parseReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    static String resourcePath(String id) {
        int colon = id.indexOf(':');
        String ns = id.substring(0, colon);
        String path = id.substring(colon + 1);
        switch (ns) {
            case "vantraya_builder":
                return "/data/vantraya_builder/worldgen/density_function/" + path + ".json";
            case "minecraft":
                return "/vanilla-1.21.1/data/minecraft/worldgen/density_function/" + path + ".json";
            default:
                throw new IllegalArgumentException("unexpected namespace in " + id);
        }
    }

    JsonElement definition(String id) {
        return definitions.computeIfAbsent(id, i -> parseResource(resourcePath(i)));
    }

    double eval(JsonElement e, double x, double y, double z) {
        if (e.isJsonPrimitive()) {
            if (e.getAsJsonPrimitive().isNumber()) {
                return e.getAsDouble();
            }
            Double fixed = overrides.get(e.getAsString());
            if (fixed != null) {
                return fixed;
            }
            return eval(definition(e.getAsString()), x, y, z);
        }
        return evalObject(e.getAsJsonObject(), x, y, z);
    }

    private double arg(JsonObject o, String key, double x, double y, double z) {
        return eval(o.get(key), x, y, z);
    }

    /**
     * Minecraft's {@code CubicSpline.Multipoint.apply}: monotone Hermite between the points, linear
     * extension outside, nested values re-evaluated at the same input. Copied formula for formula from
     * {@code net.minecraft.util.CubicSpline} so the offline terrain matches the engine's.
     */
    private double evalSpline(JsonElement s, double x, double y, double z) {
        JsonObject so = s.getAsJsonObject();
        double f = eval(so.get("coordinate"), x, y, z);
        JsonArray pts = so.getAsJsonArray("points");
        int n = pts.size();
        double[] loc = new double[n];
        double[] der = new double[n];
        JsonElement[] val = new JsonElement[n];
        for (int i = 0; i < n; i++) {
            JsonObject p = pts.get(i).getAsJsonObject();
            loc[i] = p.get("location").getAsDouble();
            der[i] = p.get("derivative").getAsDouble();
            val[i] = p.get("value");
        }
        int i = java.util.Arrays.binarySearch(loc, f);
        i = i >= 0 ? i : -i - 2; // last index with loc[i] <= f, or -1
        if (i < 0) {
            return extend(f, loc, eval(val[0], x, y, z), der, 0);
        }
        if (i == n - 1) {
            return extend(f, loc, eval(val[n - 1], x, y, z), der, n - 1);
        }
        double t = (f - loc[i]) / (loc[i + 1] - loc[i]);
        double v0 = eval(val[i], x, y, z);
        double v1 = eval(val[i + 1], x, y, z);
        double h = loc[i + 1] - loc[i];
        double e0 = der[i] * h - (v1 - v0);
        double e1 = -der[i + 1] * h + (v1 - v0);
        return (v0 + (v1 - v0) * t) + t * (1.0 - t) * (e0 + (e1 - e0) * t);
    }

    private static double extend(double f, double[] loc, double v, double[] der, int k) {
        return der[k] == 0.0 ? v : v + der[k] * (f - loc[k]);
    }

    /** Test hook: evaluate a bare spline value object. */
    double evalSplinePublic(JsonElement s, double x, double y, double z) {
        return evalSpline(s, x, y, z);
    }

    private double evalObject(JsonObject o, double x, double y, double z) {
        if (o.has("coordinate") && o.has("points") && !o.has("type")) {
            return evalSpline(o, x, y, z); // a nested spline value, without the type wrapper
        }
        String type = o.get("type").getAsString();
        switch (type) {
            case "minecraft:add":
                return arg(o, "argument1", x, y, z) + arg(o, "argument2", x, y, z);
            case "minecraft:mul":
                return arg(o, "argument1", x, y, z) * arg(o, "argument2", x, y, z);
            case "minecraft:min":
                return Math.min(arg(o, "argument1", x, y, z), arg(o, "argument2", x, y, z));
            case "minecraft:max":
                return Math.max(arg(o, "argument1", x, y, z), arg(o, "argument2", x, y, z));
            case "minecraft:abs":
                return Math.abs(arg(o, "argument", x, y, z));
            case "minecraft:square": {
                double v = arg(o, "argument", x, y, z);
                return v * v;
            }
            case "minecraft:cube": {
                double v = arg(o, "argument", x, y, z);
                return v * v * v;
            }
            case "minecraft:half_negative": {
                double v = arg(o, "argument", x, y, z);
                return v > 0.0 ? v : v * 0.5;
            }
            case "minecraft:quarter_negative": {
                double v = arg(o, "argument", x, y, z);
                return v > 0.0 ? v : v * 0.25;
            }
            case "minecraft:squeeze": {
                double c = Math.max(-1.0, Math.min(1.0, arg(o, "argument", x, y, z)));
                return c / 2.0 - c * c * c / 24.0;
            }
            case "minecraft:clamp": {
                double v = arg(o, "input", x, y, z);
                return Math.max(o.get("min").getAsDouble(), Math.min(o.get("max").getAsDouble(), v));
            }
            case "minecraft:y_clamped_gradient": {
                double fromY = o.get("from_y").getAsDouble();
                double toY = o.get("to_y").getAsDouble();
                double t = Math.max(0.0, Math.min(1.0, (y - fromY) / (toY - fromY)));
                double a = o.get("from_value").getAsDouble();
                double b = o.get("to_value").getAsDouble();
                return a + (b - a) * t;
            }
            case "minecraft:range_choice": {
                double v = arg(o, "input", x, y, z);
                boolean in = v >= o.get("min_inclusive").getAsDouble() && v < o.get("max_exclusive").getAsDouble();
                return arg(o, in ? "when_in_range" : "when_out_of_range", x, y, z);
            }
            case "minecraft:interpolated":
            case "minecraft:flat_cache":
            case "minecraft:cache_2d":
            case "minecraft:cache_once":
            case "minecraft:cache_all_in_cell":
            case "minecraft:blend_density":
                return arg(o, "argument", x, y, z);
            case "minecraft:blend_alpha":
                return 1.0; // no neighbour-chunk blending far from a world edge
            case "minecraft:blend_offset":
                return 0.0;
            case "minecraft:spline":
                return evalSpline(o.get("spline"), x, y, z);
            case "minecraft:noise":
            case "minecraft:shifted_noise":
            case "minecraft:weird_scaled_sampler":
            case "minecraft:old_blended_noise":
                return 0.0; // the mean of a noise
            case "vantraya_builder:field": {
                VantrayaModel.Fields f = model.sample(x, z);
                switch (o.get("channel").getAsString()) {
                    case "continents":
                        return f.cont();
                    case "erosion":
                        return f.erosion();
                    case "ridges":
                        return f.ridges();
                    case "temperature":
                        return f.temperature();
                    case "humidity":
                        return f.humidity();
                    case "protect":
                        return f.protect();
                    default:
                        throw new IllegalArgumentException("unknown channel " + o);
                }
            }
            default:
                throw new IllegalArgumentException("density function type not supported by the test interpreter: " + type);
        }
    }

    /** Every string reference reachable from {@code e} resolves to a definition (throws otherwise). */
    void resolveAll(JsonElement e) {
        if (e.isJsonPrimitive() && e.getAsJsonPrimitive().isString()) {
            resolveAll(definition(e.getAsString()));
        } else if (e.isJsonObject()) {
            for (Map.Entry<String, JsonElement> entry : e.getAsJsonObject().entrySet()) {
                String key = entry.getKey();
                if (key.equals("type") || key.equals("noise") || key.equals("seed_noise") || key.equals("channel")
                        || key.equals("rarity_value_mapper")) {
                    continue; // type ids, noise ids and enum names are not density-function references
                }
                resolveAll(entry.getValue());
            }
        } else if (e.isJsonArray()) {
            e.getAsJsonArray().forEach(this::resolveAll);
        }
    }
}
