import com.universal.routercontrol.definitions.Definition;
import com.universal.routercontrol.definitions.DefinitionRepository;
import com.universal.routercontrol.definitions.Json;
import com.universal.routercontrol.definitions.Picker;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;

/**
 * Renders the picker from a real registry/index.json and resolves a pick all the
 * way to a loaded Definition. No network, no device: this is the screen flow.
 *
 * Usage: java PickerSelftest <registry/index.json> <registry dir>
 */
public final class PickerSelftest {

    private static int failures;

    public static void main(String[] args) throws Exception {
        String indexPath = args[0];
        String registryDir = args[1];

        Map<String, Object> doc = Json.parseObject(
                new String(Files.readAllBytes(Paths.get(indexPath)), StandardCharsets.UTF_8));
        Picker p = Picker.fromIndex(doc);

        if (p.isEmpty()) fail("picker is empty");
        System.out.println("1. pick vendor");
        for (String v : p.vendors()) {
            System.out.println("     " + p.vendorName(v) + "   [" + v + "]  "
                    + p.models(v).size() + " model(s)");
        }

        System.out.println("2. pick model");
        for (String v : p.vendors()) {
            for (Picker.Model m : p.models(v)) {
                System.out.println("     " + p.vendorName(v) + " / " + m.name
                        + "   " + m.variantCount + " firmware line(s)");
            }
        }

        System.out.println("3. pick firmware");
        for (String v : p.vendors()) {
            for (Picker.Model m : p.models(v)) {
                for (DefinitionRepository.Variant var : p.variants(v, m.slug)) {
                    System.out.println("     " + var.label + "  ->  " + var.path);
                }
            }
        }

        // resolve one pick fully, the way the app does after the user taps
        String vendor = p.vendors().get(0);
        Picker.Model model = p.models(vendor).get(0);
        List<DefinitionRepository.Variant> vars = p.variants(vendor, model.slug);
        if (vars.isEmpty()) {
            fail("no variants for " + vendor + "/" + model.slug);
        } else {
            DefinitionRepository.Variant chosen = vars.get(0);
            DefinitionRepository.Variant again = p.variant(vendor, model.slug, chosen.path);
            if (again == null) fail("variant() could not resolve " + chosen.path);

            String apiPath = registryDir + "/" + chosen.path + "/api.json";
            String capsPath = registryDir + "/" + chosen.path + "/capabilities.json";
            Definition d = Definition.of(
                    new String(Files.readAllBytes(Paths.get(apiPath)), StandardCharsets.UTF_8),
                    new String(Files.readAllBytes(Paths.get(capsPath)), StandardCharsets.UTF_8));
            System.out.println("4. loaded " + d.vendor() + " " + d.model() + " " + d.variant()
                    + " definition " + d.definitionVersion() + ", " + d.ids().size() + " endpoints");
            if (!chosen.definitionVersion.equals(d.definitionVersion())) {
                fail("index says " + chosen.definitionVersion + ", file says " + d.definitionVersion());
            }
            System.out.println("5. features the UI can draw: "
                    + d.featureState("wifi.basic") + " / " + d.featureState("vpn.server"));
        }

        // an unknown pick must return null, never a different router
        if (p.variant("nope", "nope", "nope") != null) fail("unknown vendor resolved to a variant");

        System.out.println(failures == 0 ? "picker ok" : failures + " failure(s)");
        System.exit(failures == 0 ? 0 : 1);
    }

    private static void fail(String m) {
        failures++;
        System.out.println("     FAIL " + m);
    }
}
