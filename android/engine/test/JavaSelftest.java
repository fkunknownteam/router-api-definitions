import com.universal.routercontrol.definitions.Definition;
import com.universal.routercontrol.definitions.Json;
import com.universal.routercontrol.definitions.RouterClient;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Drives the Java engine against the fake router, one definition at a time.
 *
 * Usage: java JavaSelftest <api.json> <port>
 *
 * Mirrors what tools/selftest.py does for the Python client, so the Android code
 * path is checked against the same documents. Exits non-zero on the first
 * disagreement so the shell script around it can report which definition failed.
 */
public final class JavaSelftest {

    private static int failures = 0;

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("usage: JavaSelftest <api.json> <port>");
            System.exit(2);
        }
        Path apiPath = Paths.get(args[0]);
        int port = Integer.parseInt(args[1]);
        String api = new String(Files.readAllBytes(apiPath), StandardCharsets.UTF_8);
        Path capsPath = apiPath.resolveSibling("capabilities.json");
        String caps = Files.exists(capsPath)
                ? new String(Files.readAllBytes(capsPath), StandardCharsets.UTF_8) : "{\"features\":{}}";

        Definition d = Definition.of(api, caps);
        System.out.println("  " + d.vendor() + "/" + d.model() + "/" + d.variant()
                + " definition " + d.definitionVersion());

        // A document whose login handshake was never captured cannot be driven.
        // Proving it refuses is the whole test for those.
        Map<String, Object> sessSpec = Json.obj(d.session(), "login");
        if (sessSpec != null && "not-captured".equals(Json.str(sessSpec, "verified"))) {
            RouterClient c0 = new RouterClient(d, "127.0.0.1", port, false);
            c0.setCredentials("admin", "admin");
            try {
                c0.login();
                fail("uncaptured login handshake was attempted");
            } catch (RouterClient.DefinitionException expected) {
                System.out.println("  ^ refused an uncaptured login handshake ("
                        + d.ids().size() + " endpoints documented)");
            }
            return;
        }

        // A challenge login needs a server that issues a key and verifies the
        // signature; the generic mock deliberately does not, so record the gap
        // rather than reporting a false failure.
        Map<String, Object> sessLogin = Json.obj(d.session(), "login");
        if (sessLogin != null && "challenge".equals(Json.str(sessLogin, "kind"))) {
            Map<String, Object> ch = Json.obj(sessLogin, "challenge");
            System.out.println("  ^ challenge handshake documented ("
                    + Json.str(Json.obj(ch, "issuer"), "endpoint") + " -> "
                    + Json.str(sessLogin, "endpoint") + "); proved by tools/handshake_test.py");
            return;
        }

        // wrong password must be refused
        RouterClient bad = new RouterClient(d, "127.0.0.1", port, false);
        bad.setCredentials("admin", "definitely-not-the-password");
        try {
            bad.login();
            fail("login accepted a wrong password");
        } catch (RouterClient.DeviceException expected) {
            // correct behaviour
        }

        RouterClient c = new RouterClient(d, "127.0.0.1", port, false);
        c.setCredentials("admin", "admin");
        c.login();

        List<String> reads = new ArrayList<>();
        List<String> writes = new ArrayList<>();
        for (String id : d.ids()) {
            Map<String, Object> ep = d.endpoint(id);
            if (d.isWrite(ep)) writes.add(id);
            else reads.add(id);
        }

        for (String id : reads) {
            if ("session.login".equals(id)) continue;
            Map<String, Object> ep = d.endpoint(id);
            try {
                Object out = c.call(id, null, false);
                String kind = responseKind(ep);
                if ("json-object".equals(kind) && !(out instanceof Map)) {
                    fail(id + " did not return an object");
                }
                if ("json-array".equals(kind) && !(out instanceof List)) {
                    fail(id + " did not return an array");
                }
            } catch (RouterClient.DeviceException e) {
                fail(id + " raised " + e.getMessage());
            } catch (RuntimeException e) {
                fail(id + " raised " + e.getClass().getSimpleName() + ": " + e.getMessage());
            }
        }

        for (String id : writes) {
            Map<String, Object> ep = d.endpoint(id);
            Map<String, Object> req = asMap(ep.get("request"));
            if (req == null) continue;
            Map<String, String> values = new LinkedHashMap<>();
            Object rawFields = req.get("fields");
            if (rawFields instanceof List) {
                for (Object o : (List<?>) rawFields) {
                    Map<String, Object> f = asMap(o);
                    if (f != null && f.get("name") instanceof String) {
                        values.put((String) f.get("name"), "1");
                    }
                }
            }
            try {
                c.call(id, values, false);
                fail(id + " ran without allowWrites");
            } catch (RouterClient.DeviceException expected) {
                // refused as required
            }
            String feature = ep.get("feature") instanceof String ? (String) ep.get("feature") : null;
            boolean supported = feature == null || d.supports(feature, true);
            boolean ran = true;
            try {
                c.call(id, values, true);
            } catch (RouterClient.DeviceException e) {
                ran = false;
            } catch (RuntimeException e) {
                ran = false;
                fail(id + " write raised " + e.getClass().getSimpleName() + ": " + e.getMessage());
            }
            if (ran != supported) {
                fail(id + (supported ? " was refused but its feature is 'full'"
                        : " ran although its feature is '" + d.featureState(feature) + "'"));
            }
        }

        System.out.println("    " + reads.size() + " read(s), " + writes.size() + " write(s)");
        System.exit(failures == 0 ? 0 : 1);
    }

    private static String responseKind(Map<String, Object> ep) {
        Map<String, Object> resp = asMap(ep.get("response"));
        Object k = resp == null ? null : resp.get("kind");
        return k instanceof String ? (String) k : "json-object";
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : null;
    }

    private static void fail(String msg) {
        failures++;
        System.out.println("    FAIL " + msg);
    }
}
