package com.universal.routercontrol.definitions;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader. The app needs to read definition documents without
 * pulling in a JSON library, so this parses into Map/List/String/Number/Boolean
 * and nothing else. It is deliberately strict: a definition file is machine
 * generated and validated in CI, so a parse error here means a corrupt download,
 * not a lenient input.
 */
public final class Json {

    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    public static Object parse(String text) {
        Json p = new Json(text);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.pos != text.length()) {
            throw new JsonException("trailing data at offset " + p.pos);
        }
        return v;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object v = parse(text);
        if (!(v instanceof Map)) throw new JsonException("expected a JSON object");
        return (Map<String, Object>) v;
    }

    private void ws() {
        while (pos < src.length()) {
            char c = src.charAt(pos);
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') pos++;
            else break;
        }
    }

    private Object value() {
        if (pos >= src.length()) throw new JsonException("unexpected end of input");
        char c = src.charAt(pos);
        switch (c) {
            case '{': return object();
            case '[': return array();
            case '"': return string();
            case 't': expect("true"); return Boolean.TRUE;
            case 'f': expect("false"); return Boolean.FALSE;
            case 'n': expect("null"); return null;
            default: return number();
        }
    }

    private Map<String, Object> object() {
        Map<String, Object> out = new LinkedHashMap<>();
        pos++; // {
        ws();
        if (pos < src.length() && src.charAt(pos) == '}') { pos++; return out; }
        while (true) {
            ws();
            if (pos >= src.length() || src.charAt(pos) != '"') {
                throw new JsonException("expected a key at offset " + pos);
            }
            String key = string();
            ws();
            if (pos >= src.length() || src.charAt(pos) != ':') {
                throw new JsonException("expected ':' at offset " + pos);
            }
            pos++;
            ws();
            out.put(key, value());
            ws();
            if (pos >= src.length()) throw new JsonException("unterminated object");
            char c = src.charAt(pos);
            if (c == ',') { pos++; continue; }
            if (c == '}') { pos++; return out; }
            throw new JsonException("expected ',' or '}' at offset " + pos);
        }
    }

    private List<Object> array() {
        List<Object> out = new ArrayList<>();
        pos++; // [
        ws();
        if (pos < src.length() && src.charAt(pos) == ']') { pos++; return out; }
        while (true) {
            ws();
            out.add(value());
            ws();
            if (pos >= src.length()) throw new JsonException("unterminated array");
            char c = src.charAt(pos);
            if (c == ',') { pos++; continue; }
            if (c == ']') { pos++; return out; }
            throw new JsonException("expected ',' or ']' at offset " + pos);
        }
    }

    private String string() {
        StringBuilder sb = new StringBuilder();
        pos++; // opening quote
        while (pos < src.length()) {
            char c = src.charAt(pos++);
            if (c == '"') return sb.toString();
            if (c != '\\') { sb.append(c); continue; }
            if (pos >= src.length()) break;
            char e = src.charAt(pos++);
            switch (e) {
                case '"': sb.append('"'); break;
                case '\\': sb.append('\\'); break;
                case '/': sb.append('/'); break;
                case 'b': sb.append('\b'); break;
                case 'f': sb.append('\f'); break;
                case 'n': sb.append('\n'); break;
                case 'r': sb.append('\r'); break;
                case 't': sb.append('\t'); break;
                case 'u':
                    if (pos + 4 > src.length()) throw new JsonException("bad \\u escape");
                    sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                    pos += 4;
                    break;
                default: throw new JsonException("bad escape \\" + e);
            }
        }
        throw new JsonException("unterminated string");
    }

    private Object number() {
        int start = pos;
        while (pos < src.length()) {
            char c = src.charAt(pos);
            if ((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E') pos++;
            else break;
        }
        if (start == pos) throw new JsonException("expected a value at offset " + pos);
        String lit = src.substring(start, pos);
        if (lit.indexOf('.') < 0 && lit.indexOf('e') < 0 && lit.indexOf('E') < 0) {
            try {
                return Long.parseLong(lit);
            } catch (NumberFormatException ignored) {
                return Double.parseDouble(lit);
            }
        }
        return Double.parseDouble(lit);
    }

    private void expect(String lit) {
        if (!src.startsWith(lit, pos)) throw new JsonException("expected " + lit + " at offset " + pos);
        pos += lit.length();
    }

    /** Typed accessors, so callers do not repeat instanceof checks. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> obj(Map<String, Object> o, String key) {
        Object v = o == null ? null : o.get(key);
        return v instanceof Map ? (Map<String, Object>) v : null;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> arr(Map<String, Object> o, String key) {
        Object v = o == null ? null : o.get(key);
        return v instanceof List ? (List<Object>) v : null;
    }

    public static List<Map<String, Object>> objArr(Map<String, Object> o, String key) {
        List<Object> raw = arr(o, key);
        List<Map<String, Object>> out = new ArrayList<>();
        if (raw != null) {
            for (Object e : raw) {
                @SuppressWarnings("unchecked")
                Map<String, Object> m = (Map<String, Object>) e;
                out.add(m);
            }
        }
        return out;
    }

    public static String str(Map<String, Object> o, String key) {
        Object v = o == null ? null : o.get(key);
        return v instanceof String ? (String) v : null;
    }

    public static boolean bool(Map<String, Object> o, String key, boolean dflt) {
        Object v = o == null ? null : o.get(key);
        return v instanceof Boolean ? (Boolean) v : dflt;
    }

    public static class JsonException extends RuntimeException {
        private static final long serialVersionUID = 1L;
        public JsonException(String m) { super(m); }
    }
}
