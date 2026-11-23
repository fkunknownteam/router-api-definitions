package com.universal.routercontrol.definitions;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * One router model + firmware variant, loaded from api.json and capabilities.json.
 *
 * Nothing in this class or in RouterClient knows a vendor name. If a behaviour is
 * not described by the document, the engine refuses rather than guesses.
 */
public final class Definition {

    public final Map<String, Object> api;
    public final Map<String, Object> caps;
    private final Map<String, Map<String, Object>> byId = new LinkedHashMap<>();

    public Definition(Map<String, Object> api, Map<String, Object> caps) {
        this.api = api;
        this.caps = caps == null ? new LinkedHashMap<>() : caps;
        for (Map<String, Object> ep : Json.objArr(api, "endpoints")) {
            String id = Json.str(ep, "id");
            if (byId.containsKey(id)) {
                throw new IllegalArgumentException("duplicate endpoint id " + id);
            }
            byId.put(id, ep);
        }
    }

    public static Definition of(String apiJson, String capsJson) {
        return new Definition(Json.parseObject(apiJson),
                capsJson == null ? null : Json.parseObject(capsJson));
    }

    public String vendor() { return Json.str(api, "vendor"); }
    public String model() { return Json.str(api, "model"); }
    public String variant() { return Json.str(api, "variant"); }
    public String definitionVersion() { return Json.str(api, "definitionVersion"); }

    public Map<String, Object> endpoint(String id) {
        Map<String, Object> ep = byId.get(id);
        if (ep == null) throw new IllegalArgumentException("no endpoint " + id);
        return ep;
    }

    public boolean hasEndpoint(String id) { return byId.containsKey(id); }

    public List<String> ids() { return new ArrayList<>(byId.keySet()); }

    /** "full" | "read-only" | "unknown" | null (absent = the device cannot do it). */
    public String featureState(String feature) {
        if (feature == null) return null;
        Map<String, Object> features = Json.obj(caps, "features");
        return Json.str(features, feature);
    }

    public boolean supports(String feature, boolean write) {
        String s = featureState(feature);
        if (s == null) return false;
        return write ? "full".equals(s) : ("full".equals(s) || "read-only".equals(s));
    }

    public boolean isWrite(Map<String, Object> ep) { return Json.bool(ep, "writes", false); }

    public String risk(Map<String, Object> ep) {
        String r = Json.str(ep, "risk");
        return r == null ? "safe" : r;
    }

    public boolean needsConfirmation(Map<String, Object> ep) {
        return Json.bool(ep, "requiresConfirmation", false);
    }

    /** Longest-prefix firmware match, so a more specific definition wins. */
    public boolean matchesFirmware(String reported) {
        Map<String, Object> fw = Json.obj(api, "firmware");
        String prefix = Json.str(fw, "matchPrefix");
        String verified = Json.str(fw, "verified");
        if (prefix == null) return reported != null && reported.equals(verified);
        return reported != null && reported.startsWith(prefix);
    }

    public Map<String, Object> transport() { return Json.obj(api, "transport"); }
    public Map<String, Object> session() { return Json.obj(api, "session"); }
    public Map<String, Object> crypto() { return Json.obj(api, "crypto"); }
    public Map<String, Object> envelopes() { return Json.obj(api, "envelopes"); }
    public Map<String, Object> identify() { return Json.obj(api, "identify"); }
}
