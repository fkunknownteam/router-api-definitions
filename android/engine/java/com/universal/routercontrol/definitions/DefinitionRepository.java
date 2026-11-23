package com.universal.routercontrol.definitions;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Fetches registry/index.json, picks the variant for a device and downloads both
 * files, verifying them against the sha256 the index records.
 *
 * Caching: files are stored under <dir>/<sha256>/api.json so a new definition
 * cannot overwrite an old one and rollback means pointing at the previous hash.
 * When the network is unavailable the last verified definition stays usable.
 */
public final class DefinitionRepository {

    public static final String DEFAULT_REGISTRY =
            "https://raw.githubusercontent.com/universal-router-control/router-api-definitions/main/registry";

    private final String registryBase;
    private final String cacheDir;

    public DefinitionRepository(String registryBase, String cacheDir) {
        this.registryBase = registryBase.endsWith("/")
                ? registryBase.substring(0, registryBase.length() - 1) : registryBase;
        this.cacheDir = cacheDir;
    }

    public static final class Variant {
        public final Map<String, Object> raw;
        public final String path;
        public final String apiUrl;
        public final String capsUrl;
        public final String apiSha;
        public final String capsSha;
        public final String definitionVersion;
        public final String matchPrefix;
        public final List<String> hardwareVersions = new ArrayList<>();

        Variant(Map<String, Object> raw) {
            this.raw = raw;
            this.path = Json.str(raw, "path");
            this.apiUrl = Json.str(raw, "apiUrl");
            this.capsUrl = Json.str(raw, "capabilitiesUrl");
            this.definitionVersion = Json.str(raw, "definitionVersion");
            this.matchPrefix = Json.str(raw, "matchPrefix");
            Map<String, Object> shas = Json.obj(raw, "sha256");
            this.apiSha = Json.str(shas, "api");
            this.capsSha = Json.str(shas, "capabilities");
            for (Object h : Json.arr(raw, "hardwareVersions") == null
                    ? new ArrayList<>() : Json.arr(raw, "hardwareVersions")) {
                if (h instanceof String) hardwareVersions.add((String) h);
            }
        }

        public boolean fitsHardware(String hw) {
            return hw == null || hardwareVersions.isEmpty() || hardwareVersions.contains(hw);
        }
    }

    public List<Variant> fetchIndex() throws IOException {
        String text = httpGet(registryBase + "/index.json");
        Map<String, Object> idx = Json.parseObject(text);
        List<Variant> out = new ArrayList<>();
        for (Map<String, Object> d : Json.objArr(idx, "definitions")) {
            out.add(new Variant(d));
        }
        return out;
    }

    /**
     * Pick the definition whose firmware prefix matches, longest prefix first.
     * Returns null when nothing fits, which the caller must surface as "unknown
     * firmware" rather than falling back to a guess.
     */
    public static Variant select(List<Variant> variants, String firmware, String hardware) {
        Variant best = null;
        int bestLen = -1;
        for (Variant v : variants) {
            if (!v.fitsHardware(hardware)) continue;
            if (v.matchPrefix == null || firmware == null) continue;
            if (firmware.startsWith(v.matchPrefix) && v.matchPrefix.length() > bestLen) {
                best = v;
                bestLen = v.matchPrefix.length();
            }
        }
        return best;
    }

    /** Download, verify and cache. Returns the loaded definition. */
    public Definition fetch(Variant v) throws IOException {
        byte[] apiBytes = httpGetBytes(v.apiUrl);
        if (v.apiSha != null && !v.apiSha.equals(CipherKit.sha256Hex(apiBytes))) {
            throw new IOException("api.json sha256 mismatch for " + v.path);
        }
        byte[] capsBytes = httpGetBytes(v.capsUrl);
        if (v.capsSha != null && !v.capsSha.equals(CipherKit.sha256Hex(capsBytes))) {
            throw new IOException("capabilities.json sha256 mismatch for " + v.path);
        }
        if (cacheDir != null) {
            write(cacheDir + "/" + v.apiSha + "/api.json", apiBytes);
            write(cacheDir + "/" + v.apiSha + "/capabilities.json", capsBytes);
        }
        return Definition.of(new String(apiBytes, StandardCharsets.UTF_8),
                new String(capsBytes, StandardCharsets.UTF_8));
    }

    /** Load a previously verified copy, for offline use. */
    public Definition cached(String apiSha) throws IOException {
        return Definition.of(read(cacheDir + "/" + apiSha + "/api.json"),
                read(cacheDir + "/" + apiSha + "/capabilities.json"));
    }

    private static String httpGet(String url) throws IOException {
        return new String(httpGetBytes(url), StandardCharsets.UTF_8);
    }

    private static byte[] httpGetBytes(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) URI.create(url).toURL().openConnection();
        c.setConnectTimeout(10000);
        c.setReadTimeout(20000);
        c.setRequestProperty("Accept", "application/json");
        int status = c.getResponseCode();
        if (status != 200) throw new IOException("GET " + url + " -> " + status);
        try (InputStream in = c.getInputStream()) {
            return in.readAllBytes();
        }
    }

    private static void write(String path, byte[] data) throws IOException {
        java.io.File f = new java.io.File(path);
        java.io.File parent = f.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IOException("cannot create " + parent);
        }
        try (java.io.FileOutputStream out = new java.io.FileOutputStream(f)) {
            out.write(data);
        }
    }

    private static String read(String path) throws IOException {
        return new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(path)),
                StandardCharsets.UTF_8);
    }
}
