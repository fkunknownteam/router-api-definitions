package com.universal.routercontrol.definitions;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.CookieManager;
import java.net.HttpCookie;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Executes a Definition against a device. Contains no vendor logic: every path,
 * field name, encoding and cipher parameter is read from the document.
 *
 * The one rule this class enforces on its own is safety: a write is refused
 * unless the caller passes true, and refused again when the capabilities file
 * does not mark the backing feature "full".
 */
public final class RouterClient {

    public static class DeviceException extends Exception {
        private static final long serialVersionUID = 1L;
        public DeviceException(String m) { super(m); }
    }

    public static class DefinitionException extends RuntimeException {
        private static final long serialVersionUID = 1L;
        public DefinitionException(String m) { super(m); }
    }

    private final Definition d;
    private final String base;
    private final CookieManager cookies = new CookieManager();
    private final int timeoutMs;
    private String password;
    private String username = "admin";

    public RouterClient(Definition d, String host, int port, boolean tls) {
        this.d = d;
        Map<String, Object> t = d.transport();
        String scheme = tls ? "https" : (Json.str(t, "scheme") == null ? "http" : Json.str(t, "scheme"));
        int p = port > 0 ? port : (Json.str(t, "defaultPort") != null
                ? ((Number) t.get("defaultPort")).intValue() : ("https".equals(scheme) ? 443 : 80));
        String netloc = ("http".equals(scheme) && p == 80) || ("https".equals(scheme) && p == 443)
                ? host : host + ":" + p;
        String prefix = Json.str(t, "pathPrefix");
        this.base = scheme + "://" + netloc + (prefix == null ? "" : prefix);
        Object to = t.get("requestTimeoutMs");
        this.timeoutMs = to instanceof Number ? ((Number) to).intValue() : 12000;
    }

    public void setCredentials(String user, String password) {
        this.username = user == null ? "admin" : user;
        this.password = password;
    }

    public String baseUrl() { return base; }

    // ---------------------------------------------------------------- transport

    private HttpURLConnection open(String path, byte[] body, String method, String contentType,
                                   String referer) throws IOException {
        URL url = URI.create(base + path).toURL();
        HttpURLConnection c = (HttpURLConnection) url.openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(timeoutMs);
        c.setReadTimeout(timeoutMs);
        c.setInstanceFollowRedirects(false); // 302 is a signal in these definitions
        if (referer != null) c.setRequestProperty("Referer", referer);
        List<HttpCookie> jar = cookies.getCookieStore().getCookies();
        if (!jar.isEmpty()) {
            StringBuilder sb = new StringBuilder();
            for (HttpCookie ck : jar) {
                if (sb.length() > 0) sb.append("; ");
                sb.append(ck.getName()).append('=').append(ck.getValue());
            }
            c.setRequestProperty("Cookie", sb.toString());
        }
        if (body != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", contentType);
            c.setFixedLengthStreamingMode(body.length);
            try (OutputStream os = c.getOutputStream()) {
                os.write(body);
            }
        }
        return c;
    }

    private static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        return out.toByteArray();
    }

    private Response send(String path, byte[] body, String method, String contentType, String referer)
            throws IOException {
        HttpURLConnection c = open(path, body, method, contentType, referer);
        int status = c.getResponseCode();
        InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
        byte[] data = in == null ? new byte[0] : readAll(in);
        List<String> setCookies = c.getHeaderFields().get("Set-Cookie");
        if (setCookies != null) {
            for (String s : setCookies) {
                for (HttpCookie ck : HttpCookie.parse(s)) cookies.getCookieStore().add(null, ck);
            }
        }
        return new Response(status, data, c.getHeaderField("Location"),
                c.getHeaderField("Content-Type"));
    }

    public static final class Response {
        public final int status;
        public final byte[] body;
        public final String location;
        public final String contentType;
        Response(int s, byte[] b, String l, String ct) {
            this.status = s; this.body = b; this.location = l; this.contentType = ct;
        }
        public String text() { return new String(body, StandardCharsets.UTF_8); }
    }

    private String formEncode(Map<String, String> values) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : values.entrySet()) {
            if (sb.length() > 0) sb.append('&');
            sb.append(enc(e.getKey())).append('=').append(enc(e.getValue()));
        }
        return sb.toString();
    }

    private static String enc(String s) {
        try {
            return java.net.URLEncoder.encode(s, "UTF-8");
        } catch (java.io.UnsupportedEncodingException e) {
            throw new IllegalStateException(e);
        }
    }

    // ------------------------------------------------------------------ session

    private String mainReferer() { return base + "/main.html"; }

    private String encodePassword(String encoding) {
        String pw = password == null ? "" : password;
        if (encoding == null || "plain".equals(encoding)) return pw;
        if ("md5-hex".equals(encoding)) return CipherKit.md5Hex(pw);
        if ("base64".equals(encoding)) {
            return java.util.Base64.getEncoder().encodeToString(pw.getBytes(StandardCharsets.UTF_8));
        }
        if ("md5-hex-of-md5-hex".equals(encoding)) return CipherKit.md5Hex(CipherKit.md5Hex(pw));
        throw new DefinitionException("unsupported passwordEncoding " + encoding);
    }

    @SuppressWarnings("unchecked")
    public void login() throws IOException, DeviceException {
        Map<String, Object> sess = d.session();
        Map<String, Object> spec = Json.obj(sess, "login");
        if (spec == null) throw new DefinitionException("session.login is missing");
        if ("none".equals(Json.str(sess, "type"))) return;
        // A handshake that was never captured must not be attempted: guessing the
        // encoding spends one of the device's password attempts and cannot
        // succeed by luck. Refusing is the only honest behaviour.
        if ("not-captured".equals(Json.str(spec, "verified"))) {
            throw new DefinitionException(
                    "login handshake for " + d.model() + " " + d.variant()
                            + " was never captured");
        }

        if ("challenge".equals(Json.str(spec, "kind"))) {
            loginChallenge(spec);
            return;
        }

        Map<String, Object> bodySpec = Json.obj(spec, "body");
        Map<String, String> body = new LinkedHashMap<>();
        if (bodySpec != null) {
            for (Map.Entry<String, Object> e : bodySpec.entrySet()) {
                String v = String.valueOf(e.getValue());
                v = v.replace("{password}", encodePassword(Json.str(spec, "passwordEncoding")));
                v = v.replace("{username}", username);
                body.put(e.getKey(), v);
            }
        }
        String encoding = Json.str(spec, "passwordEncoding");
        boolean encryptLogin = CipherKit.usesAes(d) && !CipherKit.isPlaintextPath(d, Json.str(spec, "endpoint"));

        byte[] payload = formEncode(body).getBytes(StandardCharsets.UTF_8);
        String ctype = "application/x-www-form-urlencoded";
        if (encryptLogin) {
            String key = fetchKey();
            payload = encryptOrFail(formEncode(body), key).getBytes(StandardCharsets.UTF_8);
        }
        Response r = send(Json.str(spec, "endpoint"), payload,
                Json.str(spec, "method") == null ? "POST" : Json.str(spec, "method"),
                ctype, base + "/login.html");
        String text = r.text().trim();
        Map<String, Object> success = Json.obj(spec, "success");
        String kind = success == null ? "http-200" : Json.str(success, "kind");
        Map<String, Object> refusals = success == null ? null : Json.obj(success, "refusalCodes");
        String code = text.length() >= 1 && text.length() <= 4 && text.matches("\\d{1,4}") ? text : null;

        if (refusals != null && code != null && refusals.containsKey(code)) {
            throw new DeviceException("login refused: " + refusals.get(code));
        }
        boolean ok;
        if ("redirect-to-main".equals(kind)) {
            String marker = success == null ? "/login.html" : Json.str(success, "loginPageMarker");
            ok = r.status >= 300 && r.status < 400
                    && (marker == null || r.location == null || !r.location.contains(marker));
        } else if ("body-number".equals(kind)) {
            ok = code == null;
        } else if ("cookie-set".equals(kind)) {
            ok = !cookies.getCookieStore().getCookies().isEmpty();
        } else {
            ok = r.status == 200;
        }
        if (!ok) throw new DeviceException("login rejected (status " + r.status + ", reply " + text + ")");
    }

    public void logout() throws IOException {
        Map<String, Object> spec = Json.obj(d.session(), "logout");
        if (spec == null) return;
        String ep = Json.str(spec, "endpoint");
        send(ep, null, Json.str(spec, "method") == null ? "GET" : Json.str(spec, "method"),
                null, mainReferer());
        cookies.getCookieStore().removeAll();
    }

    /**
     * Execute a challenge/response login, driven entirely by the document.
     *
     * The device issues an RSA public key and a nonce; the client invents an
     * AES key, encrypts the credentials with it, and reports that key inside an
     * RSA signature. The device recovers the key by verifying the signature.
     * Nothing here is device-specific.
     */
    private void loginChallenge(Map<String, Object> spec) throws IOException, DeviceException {
        Map<String, Object> ch = Json.obj(spec, "challenge");
        if (ch == null) throw new DefinitionException("session.login.challenge is missing");
        Map<String, Object> iss = Json.obj(ch, "issuer");
        Map<String, Object> cipher = Json.obj(ch, "cipher");
        Map<String, Object> sig = Json.obj(ch, "signature");
        if (iss == null || cipher == null || sig == null) {
            throw new DefinitionException("challenge.issuer, cipher and signature are all required");
        }

        // 1. Ask for the public key and nonce.
        Response r = send(Json.str(iss, "endpoint"), null,
                orDefault(Json.str(iss, "method"), "GET"), null, null);
        String text = new String(r.body, StandardCharsets.UTF_8);
        Map<String, Object> extract = Json.obj(iss, "extract");
        String nn = extractValue(text, extract, "nn");
        String ee = extractValue(text, extract, "ee");
        String seq = extractValue(text, extract, "seq");
        if (nn == null || nn.isEmpty()) {
            throw new DefinitionException(d.model() + ": " + Json.str(iss, "endpoint")
                    + " returned no RSA modulus; cannot build a challenge");
        }

        // 2. Credentials, hashed the way the document says.
        String hashKind = Json.str(ch, "hash");
        if (!"md5-hex-of-username-plus-password".equals(hashKind)) {
            throw new DefinitionException("unsupported challenge hash " + hashKind);
        }
        String user = username == null || username.isEmpty() ? "admin" : username;
        String pwd = password == null ? "" : password;
        String hashHex = CipherKit.md5Hex(user + pwd);

        String payload;
        if ("json".equals(Json.str(spec, "payloadEncoding"))) {
            payload = "{\"username\":\"" + user + "\",\"password\":\"" + pwd + "\"}";
        } else {
            payload = "username=" + user + "&password=" + hashHex;
        }

        // 3. Invent the AES key the device will recover from our signature.
        if (!"aes-128-cbc-base64".equals(Json.str(cipher, "type"))) {
            throw new DefinitionException("unsupported challenge cipher " + Json.str(cipher, "type"));
        }
        if (!"digits".equals(Json.str(cipher, "charset"))) {
            throw new DefinitionException("unsupported key charset " + Json.str(cipher, "charset"));
        }
        String key = randomDigits(intOf(cipher, "keyChars", 16));
        String iv = randomDigits(intOf(cipher, "ivChars", 16));
        String keyReport = orDefault(Json.str(cipher, "keyReport"), "key=%key%&iv=%iv%")
                .replace("%key%", key).replace("%iv%", iv);

        String data;
        try {
            javax.crypto.Cipher c = javax.crypto.Cipher.getInstance("AES/CBC/PKCS5Padding");
            c.init(javax.crypto.Cipher.ENCRYPT_MODE,
                    new javax.crypto.spec.SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "AES"),
                    new javax.crypto.spec.IvParameterSpec(iv.getBytes(StandardCharsets.UTF_8)));
            // Portable base64: the engine is also compiled and tested outside
            // Android, where android.util.Base64 is unavailable.
            data = java.util.Base64.getEncoder().encodeToString(
                    c.doFinal(payload.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new DefinitionException("AES unavailable: " + e.getMessage());
        }

        // 4. Sign the rendezvous string so the device learns the key.
        if (!"rsa-pkcs1-v1_5".equals(Json.str(sig, "type"))) {
            throw new DefinitionException("unsupported signature " + Json.str(sig, "type"));
        }
        String plain = orDefault(Json.str(sig, "plaintext"),
                "%keyReport%&h=%hash%&s=%seqPlusDataLen%")
                .replace("%keyReport%", keyReport)
                .replace("%hash%", hashHex)
                .replace("%seqPlusDataLen%",
                        String.valueOf((seq == null ? 0L : Long.parseLong(seq.trim())) + data.length()));
        String sign = rsaEncryptHex(plain, nn, orDefault(ee, "010001"), intOf(sig, "bits", 512));

        // 5. Frame and send.
        String envelope = orDefault(Json.str(ch, "envelope"), "sign=%sign%\r\ndata=%data%\r\n");
        byte[] body = envelope.replace("%sign%", sign).replace("%data%", data)
                .getBytes(StandardCharsets.UTF_8);
        Response post = send(Json.str(spec, "endpoint"), body,
                orDefault(Json.str(spec, "method"), "POST"),
                "application/x-www-form-urlencoded", null);
        String reply = new String(post.body, StandardCharsets.UTF_8).trim();
        if (post.status >= 400) {
            throw new DeviceException("login rejected (status " + post.status
                    + ", reply " + abbreviate(reply) + ")");
        }
        String low = reply.toLowerCase().replace(" ", "");
        if (low.contains("\"valid\":false") || low.equals("0") || low.isEmpty()) {
            throw new DeviceException("login rejected by device: " + abbreviate(reply));
        }
    }

    /** Read nn/ee/seq out of the issuer reply, per the document's map. */
    private static String extractValue(String text, Map<String, Object> extract, String which) {
        if (extract == null) return null;
        Map<String, Object> map = Json.obj(extract, "map");
        String field = map == null ? which : orDefault(Json.str(map, which), which);
        if ("json".equals(Json.str(extract, "format"))) {
            Object cur = Json.parse(text);
            for (String part : field.split("\\.")) {
                if (!(cur instanceof Map)) return null;
                cur = ((Map<?, ?>) cur).get(part);
                if (cur == null) return null;
            }
            return cur instanceof Number
                    ? String.valueOf(((Number) cur).longValue()) : String.valueOf(cur);
        }
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("\\b" + java.util.regex.Pattern.quote(field)
                        + "\\s*=\\s*[\"']?([0-9a-fA-Fx]+)[\"']?").matcher(text);
        return m.find() ? m.group(1) : null;
    }

    /**
     * RSA PKCS#1 v1.5 type-2, split into (k-11)-byte blocks, hex out.
     * Mirrors the Tom Wu port the vendor UI ships.
     */
    private static String rsaEncryptHex(String data, String nnHex, String eeHex, int bits) {
        java.math.BigInteger n = new java.math.BigInteger(nnHex, 16);
        java.math.BigInteger e = new java.math.BigInteger(eeHex, 16);
        byte[] raw = data.getBytes(StandardCharsets.UTF_8);
        int k = (n.bitLength() + 7) / 8;
        int step = k - 11;
        if (step <= 0) throw new DefinitionException("RSA modulus too small (" + k + " bytes)");
        java.util.Random rnd = new java.util.Random();
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < raw.length; i += step) {
            int len = Math.min(step, raw.length - i);
            byte[] block = new byte[k];
            block[1] = 0x02;
            int psEnd = k - 3 - len;
            for (int p = 2; p <= psEnd; p++) {
                int b;
                do { b = rnd.nextInt(256); } while (b == 0);
                block[p] = (byte) b;
            }
            System.arraycopy(raw, i, block, k - len, len);
            String hex = new java.math.BigInteger(1, block).modPow(e, n).toString(16);
            for (int p = hex.length(); p < bits / 4; p++) out.append('0');
            out.append(hex);
        }
        return out.toString();
    }

    private static String randomDigits(int count) {
        java.util.Random rnd = new java.util.Random();
        StringBuilder sb = new StringBuilder(count);
        for (int i = 0; i < count; i++) sb.append((char) ('0' + rnd.nextInt(10)));
        return sb.toString();
    }

    private static String abbreviate(String s) {
        return s.length() <= 60 ? s : s.substring(0, 60) + "...";
    }

    private static String orDefault(String v, String fallback) {
        return v == null || v.isEmpty() ? fallback : v;
    }

    private static int intOf(Map<String, Object> m, String key, int fallback) {
        Object v = m == null ? null : m.get(key);
        return v instanceof Number ? ((Number) v).intValue() : fallback;
    }

    /** Fetch the per-request key from the endpoint the definition names. */
    public String fetchKey() throws IOException {
        Map<String, Object> crypto = d.crypto();
        if (crypto == null) return null;
        Map<String, Object> src = Json.obj(crypto, "keySource");
        if (src == null || !"endpoint-field".equals(Json.str(src, "kind"))) {
            throw new DefinitionException("unsupported keySource");
        }
        Response r = send(Json.str(src, "endpoint"), null, "GET", null, mainReferer());
        Map<String, Object> doc = Json.parseObject(r.text());
        Object v = doc.get(Json.str(src, "field"));
        if (!(v instanceof String)) {
            throw new DefinitionException("key field " + Json.str(src, "field") + " missing from reply");
        }
        return (String) v;
    }

    // -------------------------------------------------------------------- calls

    /** Execute one endpoint. Returns the decoded object, list or text wrapper. */
    public Object call(String id, Map<String, String> values, boolean allowWrites)
            throws IOException, DeviceException {
        Map<String, Object> ep = d.endpoint(id);
        boolean writes = d.isWrite(ep);
        if (writes && !allowWrites) {
            throw new DeviceException(id + " changes device state; allowWrites must be true");
        }
        String feature = Json.str(ep, "feature");
        if (writes && feature != null && !d.supports(feature, true)) {
            throw new DeviceException(id + ": definition marks feature " + feature + " as "
                    + d.featureState(feature) + ", so the write is not attempted");
        }

        Map<String, Object> crypto = d.crypto();
        String path = Json.str(ep, "path");
        boolean needsKey = crypto != null && !CipherKit.isPlaintextPath(d, path);
        String key = needsKey ? fetchKey() : null;

        byte[] payload = buildBody(ep, values == null ? new LinkedHashMap<>() : values, key);
        String method = Json.str(ep, "method");
        Response r;
        if (payload == null) {
            r = send(path, null, method, null, mainReferer());
        } else {
            String ctype = bodyContentType(ep, values);
            r = send(path, payload, method, ctype, mainReferer());
        }

        String text = r.text();
        Map<String, Object> expiry = Json.obj(d.session(), "expiry");
        if (expiry != null) {
            List<Object> codes = Json.arr(expiry, "statusCodes");
            boolean expired = false;
            if (codes != null) {
                for (Object c : codes) {
                    if (c instanceof Number && ((Number) c).intValue() == r.status) expired = true;
                }
            }
            List<Object> markers = Json.arr(expiry, "bodyMarkers");
            if (markers != null) {
                for (Object m : markers) {
                    if (m instanceof String && text.contains((String) m)) expired = true;
                }
            }
            if (expired && !"none".equals(Json.str(d.session(), "type"))) {
                throw new DeviceException(id + ": session expired (status " + r.status + ")");
            }
        }
        return decode(ep, r, text, key);
    }

    private String bodyContentType(Map<String, Object> ep, Map<String, String> values) {
        Map<String, Object> req = Json.obj(ep, "request");
        String enc = req == null ? "none" : Json.str(req, "bodyEncoding");
        if ("json".equals(enc)) return "application/json";
        return "application/x-www-form-urlencoded";
    }

    private byte[] buildBody(Map<String, Object> ep, Map<String, String> values, String key) {
        Map<String, Object> req = Json.obj(ep, "request");
        String enc = req == null ? "none" : Json.str(req, "bodyEncoding");
        if (enc == null || "none".equals(enc)) {
            if (!values.isEmpty()) {
                throw new DefinitionException(Json.str(ep, "id") + " takes no body");
            }
            return null;
        }
        List<Map<String, Object>> fields = Json.objArr(req, "fields");
        Map<String, Map<String, Object>> allowed = new LinkedHashMap<>();
        for (Map<String, Object> f : fields) allowed.put(Json.str(f, "name"), f);
        for (String k : values.keySet()) {
            if (!allowed.containsKey(k)) {
                throw new DefinitionException(Json.str(ep, "id") + ": field " + k + " is not declared");
            }
        }
        for (Map<String, Object> f : fields) {
            if (Json.bool(f, "required", false) && !values.containsKey(Json.str(f, "name"))) {
                throw new DefinitionException(Json.str(ep, "id") + ": required field "
                        + Json.str(f, "name") + " missing");
            }
        }
        String plain = "json".equals(enc) ? jsonObject(values) : formEncode(values);
        if ("encrypted-form".equals(enc)) {
            return encryptOrFail(plain, key).getBytes(StandardCharsets.UTF_8);
        }
        if ("json".equals(enc) && key != null) {
            return encryptOrFail(plain, key).getBytes(StandardCharsets.UTF_8);
        }
        return plain.getBytes(StandardCharsets.UTF_8);
    }

    private Object decode(Map<String, Object> ep, Response r, String text, String key) {
        Map<String, Object> resp = Json.obj(ep, "response");
        String kind = resp == null ? "json-object" : Json.str(resp, "kind");
        if (kind == null) kind = "json-object";
        if ("text".equals(kind) || "html".equals(kind) || "redirect".equals(kind) || "binary".equals(kind)) {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("kind", kind);
            out.put("status", (long) r.status);
            out.put("text", text.trim());
            return out;
        }
        String body = text.trim();
        if (key != null) {
            try {
                body = new String(CipherKit.decrypt(d, body, key), StandardCharsets.UTF_8);
            } catch (GeneralSecurityException e) {
                throw new DefinitionException("reply did not decrypt with the declared key: "
                        + e.getMessage());
            }
        }
        Object parsed = Json.parse(body);
        Map<String, Object> env = d.envelopes();
        String resultField = env == null ? null : Json.str(env, "resultField");
        if (resultField != null && parsed instanceof Map && ((Map<?, ?>) parsed).containsKey(resultField)) {
            Object code = ((Map<?, ?>) parsed).get(resultField);
            Object success = env.get("successValue");
            long want = success instanceof Number ? ((Number) success).longValue() : 0L;
            long got = code instanceof Number ? ((Number) code).longValue() : Long.MIN_VALUE;
            if (got != want) {
                throw new DefinitionException(Json.str(ep, "id") + ": device returned "
                        + resultField + "=" + code);
            }
        }
        return parsed;
    }

    private String encryptOrFail(String plain, String key) {
        try {
            return CipherKit.encrypt(d, plain, key);
        } catch (GeneralSecurityException e) {
            throw new DefinitionException("cannot encrypt with the declared key: " + e.getMessage());
        }
    }

    private static String jsonObject(Map<String, String> values) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, String> e : values.entrySet()) {
            if (!first) sb.append(',');
            first = false;
            sb.append('"').append(e.getKey().replace("\\", "\\\\").replace("\"", "\\\"")).append("\":\"");
            sb.append(e.getValue().replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
        }
        return sb.append('}').toString();
    }
}
