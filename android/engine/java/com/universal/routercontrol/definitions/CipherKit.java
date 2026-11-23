package com.universal.routercontrol.definitions;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * AES helpers driven entirely by definition["crypto"]. No key material is
 * compiled in: the key comes from the endpoint the definition names, on every
 * request, because these firmwares regenerate it per call.
 */
public final class CipherKit {

    private CipherKit() {}

    public static boolean usesAes(Definition d) {
        Map0 c = new Map0(d.crypto());
        return c.has() && "aes-128-cbc-base64".equals(c.str("type"));
    }

    public static boolean isPlaintextPath(Definition d, String path) {
        if (d.crypto() == null) return true;
        java.util.List<Object> plain = Json.arr(d.crypto(), "plaintextPaths");
        if (plain == null) return false;
        for (Object p : plain) {
            if (path.equals(p)) return true;
        }
        return false;
    }

    public static byte[] decrypt(Definition d, String base64, String key)
            throws GeneralSecurityException {
        Map0 c = new Map0(d.crypto());
        byte[] raw = java.util.Base64.getDecoder().decode(base64.trim());
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        cipher.init(Cipher.DECRYPT_MODE, keySpec(key), ivSpec(c.str("iv")));
        return cipher.doFinal(raw);
    }

    public static String encrypt(Definition d, String plaintext, String key)
            throws GeneralSecurityException {
        Map0 c = new Map0(d.crypto());
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        cipher.init(Cipher.ENCRYPT_MODE, keySpec(key), ivSpec(c.str("iv")));
        byte[] out = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));
        return java.util.Base64.getEncoder().encodeToString(out);
    }

    private static SecretKeySpec keySpec(String key) {
        byte[] k = key.getBytes(StandardCharsets.US_ASCII);
        if (k.length != 16 && k.length != 24 && k.length != 32) {
            throw new IllegalArgumentException("key must be 16, 24 or 32 ASCII bytes, got " + k.length);
        }
        return new SecretKeySpec(k, "AES");
    }

    private static IvParameterSpec ivSpec(String iv) {
        byte[] v = iv.getBytes(StandardCharsets.US_ASCII);
        if (v.length != 16) throw new IllegalArgumentException("iv must be 16 ASCII bytes");
        return new IvParameterSpec(v);
    }

    public static String md5Hex(String s) {
        try {
            byte[] d = MessageDigest.getInstance("MD5").digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    public static String sha256Hex(byte[] data) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(data);
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Small wrapper so null-heavy map reads stay readable. */
    @SuppressWarnings("unused")
    private static final class Map0 {
        private final java.util.Map<String, Object> m;
        Map0(java.util.Map<String, Object> m) { this.m = m; }
        boolean has() { return m != null; }
        String str(String k) { return m == null ? null : Json.str(m, k); }
    }
}
