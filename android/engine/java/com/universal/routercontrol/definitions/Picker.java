package com.universal.routercontrol.definitions;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The vendor -> model -> firmware picker, built straight from registry/index.json.
 *
 * Nothing here is hardcoded: a vendor appears because a definition declares it,
 * a model appears because a directory exists, and a firmware option appears
 * because a variant does. Adding a vendor to the registry adds it to the picker
 * with no app release.
 *
 * This is the fast path the user asked for: the index is one small request, it is
 * cached, and the three lists render from it immediately. The user picks; the app
 * then downloads exactly that variant instead of guessing which one is on the
 * other end of the wire.
 */
public final class Picker {

    /** One row in the model list. */
    public static final class Model {
        public final String slug;
        public final String name;
        public final int variantCount;
        final List<DefinitionRepository.Variant> variants;

        Model(String slug, String name, int variantCount, List<DefinitionRepository.Variant> variants) {
            this.slug = slug;
            this.name = name;
            this.variantCount = variantCount;
            this.variants = variants;
        }
    }

    private final Map<String, String> vendorNames = new LinkedHashMap<>();
    private final Map<String, List<Model>> byVendor = new LinkedHashMap<>();

    private Picker() {}

    /** Build from the already-parsed index document. */
    public static Picker fromIndex(Map<String, Object> index) {
        Picker p = new Picker();
        Map<String, Object> names = Json.obj(index, "vendorNames");
        if (names != null) {
            for (Map.Entry<String, Object> e : names.entrySet()) {
                if (e.getValue() instanceof String) p.vendorNames.put(e.getKey(), (String) e.getValue());
            }
        }
        Map<String, Object> vendors = Json.obj(index, "vendors");
        if (vendors == null) return p;
        for (Map.Entry<String, Object> ve : vendors.entrySet()) {
            String vendor = ve.getKey();
            List<Model> models = new ArrayList<>();
            if (ve.getValue() instanceof List) {
                for (Object mo : (List<?>) ve.getValue()) {
                    if (!(mo instanceof Map)) continue;
                    @SuppressWarnings("unchecked")
                    Map<String, Object> m = (Map<String, Object>) mo;
                    List<DefinitionRepository.Variant> variants = new ArrayList<>();
                    for (Map<String, Object> v : Json.objArr(m, "variants")) {
                        variants.add(new DefinitionRepository.Variant(v));
                    }
                    Object count = m.get("variantCount");
                    models.add(new Model(
                            Json.str(m, "slug"),
                            Json.str(m, "model"),
                            count instanceof Number ? ((Number) count).intValue() : variants.size(),
                            variants));
                }
            }
            if (!models.isEmpty()) {
                p.byVendor.put(vendor, models);
                p.vendorNames.putIfAbsent(vendor, Character.toUpperCase(vendor.charAt(0)) + vendor.substring(1));
            }
        }
        return p;
    }

    /** Step 1 of the picker: every vendor that has at least one definition. */
    public List<String> vendors() {
        return new ArrayList<>(byVendor.keySet());
    }

    /** Display name for a vendor, e.g. "Tenda". */
    public String vendorName(String vendor) {
        String n = vendorNames.get(vendor);
        return n == null ? vendor : n;
    }

    /** Step 2: models for a vendor, e.g. ["AC10"]. */
    public List<Model> models(String vendor) {
        List<Model> m = byVendor.get(vendor);
        return m == null ? new ArrayList<>() : m;
    }

    /** Step 3: firmware variants for a model, newest hardware last. */
    public List<DefinitionRepository.Variant> variants(String vendor, String modelSlug) {
        for (Model m : models(vendor)) {
            if (m.slug != null && m.slug.equalsIgnoreCase(modelSlug)) return m.variants;
        }
        return new ArrayList<>();
    }

    /**
     * Resolve a pick straight to one variant. Returns null when the vendor or
     * model is unknown, so the caller can say so instead of silently falling
     * back to a different router.
     */
    public DefinitionRepository.Variant variant(String vendor, String modelSlug, String variantPath) {
        for (DefinitionRepository.Variant v : variants(vendor, modelSlug)) {
            if (v.path != null && v.path.equals(variantPath)) return v;
        }
        return null;
    }

    /**
     * The picker with no network: whatever index was cached last time. This is
     * what makes the first screen instant on a phone that is joined to a router
     * with no internet.
     */
    public static Picker fromCache(String cacheDir) {
        try {
            String text = DefinitionRepository.readCachedIndex(cacheDir);
            if (text == null) return new Picker();
            return fromIndex(Json.parseObject(text));
        } catch (IOException | Json.JsonException e) {
            return new Picker();
        }
    }

    /** True when the cache holds something the picker can render. */
    public boolean isEmpty() {
        return byVendor.isEmpty();
    }
}
